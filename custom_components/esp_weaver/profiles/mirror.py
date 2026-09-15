# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""A local copy of the profile repository, kept under ``.storage``.

The copy holds the repository's index - every product, a few hundred bytes
each - and the content of the products this household has picked. The list
is built from the copy, never from the network. Going online refreshes the
index and re-downloads content whose hash moved; picking a product downloads
that one file, if the copy does not hold it already. A repository that cannot
be reached leaves the copy as it was.

The copy normally follows the published index. It can be told to follow a
pull request on GitHub instead (``github.py``), for a vendor trying a profile
before it merges; the choice is kept in the copy until cleared, so that every
refresh - at start, on reload, in the flows - reads the same place.
"""

import asyncio
from datetime import UTC, datetime
import logging
from typing import Any, Final

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..data import domain_data
from ..identity import DOMAIN
from .download import MAX_PROFILE_SIZE, DownloadError, async_fetch_text
from .github import UPSTREAM_REPO, RefError, RepositoryRef, async_read_ref
from .index import GITHUB_RAW, INDEX_FILE, MAX_INDEX_SIZE, parse_index
from .product import CATEGORY_UNKNOWN, SOURCE_REPO, ProductCandidate
from .schema import ProfileError, parse_profile, validate_profile

_LOGGER = logging.getLogger(__name__)

# The profile repository, ``espressif/esp-weaver-profiles`` on GitHub, read
# as raw files: `index.json` at the root, each product at its index `path`. A
# tuple so that a second base serving the same tree can be added; they are
# tried in order, the first to answer used.
PROFILE_SOURCES: Final[tuple[str, ...]] = (f"{GITHUB_RAW}/{UPSTREAM_REPO}/master/",)

STORAGE_KEY: Final = f"{DOMAIN}.repository"
STORAGE_VERSION: Final = 1
KEY_PRODUCTS: Final = "products"
KEY_SYNCED_AT: Final = "synced_at"
# The pull request followed instead of the published index, as typed
# (``RepositoryRef.spec``), and the commit its index was last read at.
KEY_SOURCE: Final = "source"
KEY_SOURCE_COMMIT: Final = "source_commit"
# Hash of the held content as written when it was downloaded. Kept beside
# the validated content so a schema change cannot make held content look new.
KEY_HELD_HASH: Final = "held_hash"

# A refresh happens while the user looks at a form, so each source gets this
# long for the index, and each changed file this long; a single file fetched
# on its own is allowed longer.
INDEX_FETCH_TIMEOUT: Final = ClientTimeout(total=4)
SYNC_FETCH_TIMEOUT: Final = ClientTimeout(total=10)
FETCH_TIMEOUT: Final = ClientTimeout(total=15)
# Changed files are downloaded together, this many at a time.
MAX_CONCURRENT_DOWNLOADS: Final = 6


class CatalogError(Exception):
    """Raised when a product cannot be fetched or is not a profile."""


async def _fetch_text(
    hass: HomeAssistant, url: str, limit: int, timeout: ClientTimeout = FETCH_TIMEOUT
) -> str:
    """Download one file from the repository.

    Raises:
        CatalogError: The download failed, was refused, or was oversized.
    """
    try:
        return await async_fetch_text(hass, url, limit, timeout)
    except DownloadError as err:
        cause = err.__cause__
        prefix = f"{type(cause).__name__} fetching" if cause is not None else "fetching"
        raise CatalogError(f"{prefix} {url}: {err}") from err


def _parse_index(text: str, base: str) -> list[ProductCandidate]:
    """Turn the published index into candidates that know where their file is.

    Raises:
        CatalogError: The document is not an index this version reads.
    """
    try:
        return parse_index(text, base).products
    except ProfileError as err:
        raise CatalogError(f"index at {base}: {err}") from err


async def async_fetch_index(hass: HomeAssistant) -> tuple[list[ProductCandidate], str]:
    """List the repository, trying each source in turn.

    Returns:
        The candidates and an empty string, or nothing and why.
    """
    errors: list[str] = []
    for base in PROFILE_SOURCES:
        try:
            text = await _fetch_text(
                hass, base + INDEX_FILE, MAX_INDEX_SIZE, INDEX_FETCH_TIMEOUT
            )
            listed = await hass.async_add_executor_job(_parse_index, text, base)
        except CatalogError as err:
            _LOGGER.debug("Profile repository not read: %s", err)
            errors.append(str(err))
        else:
            return listed, ""
    return [], "; ".join(errors)


async def async_download_candidate(
    hass: HomeAssistant,
    candidate: ProductCandidate,
    timeout: ClientTimeout = FETCH_TIMEOUT,
) -> ProductCandidate:
    """Return a listed candidate with its content downloaded.

    The file must validate and hash to what the index promised.

    Raises:
        CatalogError: The product could not be fetched or is not a profile.
    """
    if not candidate.url:
        raise CatalogError(f"{candidate.id} has neither content nor a URL")

    text = await _fetch_text(hass, candidate.url, MAX_PROFILE_SIZE, timeout)
    try:
        parsed = await hass.async_add_executor_job(parse_profile, text, candidate.id)
    except ProfileError as err:
        raise CatalogError(str(err)) from err

    fetched = ProductCandidate.from_parsed(
        parsed,
        source=candidate.source,
        location=candidate.location,
        url=candidate.url,
    )
    # A file listed under one id that comes back as another product is
    # refused like a wrong hash.
    if fetched.id != candidate.id or fetched.hash != candidate.hash:
        raise CatalogError(
            f"{candidate.id} downloaded from {candidate.url} does not match the "
            f"index (expected {candidate.id}@{candidate.hash}, "
            f"got {fetched.id}@{fetched.hash})"
        )
    return fetched


class RepositoryMirror:
    """The repository's index, and the content of the products looked at.

    One record per product id. Every record carries what the index said -
    label, model, path, url and the hash the file is promised to have. A
    record has ``content`` once the file was downloaded; it is stale when the
    content's hash is no longer the promised one, and is offered as it is
    until the new file is fetched.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Wrap Home Assistant's store without reading it yet."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._products: dict[str, dict[str, Any]] = {}
        self.synced_at: str | None = None
        # What the copy follows: None for the published index, else the pull
        # request as typed; and the commit its index was last read at.
        self.source: str | None = None
        self.source_commit: str | None = None
        # Why the last refresh this run fell short, or empty: not asked yet,
        # or the copy is what the repository lists.
        self.sync_error: str = ""
        self._loaded = False

    async def async_load(self) -> None:
        """Read the copy, once, dropping whatever today's schema refuses."""
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        products = data.get(KEY_PRODUCTS)
        self._products = {}
        for key, record in (products if isinstance(products, dict) else {}).items():
            if not isinstance(record, dict):
                _LOGGER.warning("Repository copy of %s is dropped: not a record", key)
                continue
            content = record.get("content")
            if content is not None:
                try:
                    content = validate_profile(content, f"repository copy of {key}")
                except ProfileError as err:
                    # The schema moves between releases; the product stays
                    # listed and its file is downloaded again when wanted.
                    _LOGGER.warning("Repository copy of %s is dropped: %s", key, err)
                    content = None
            listing = {**record, "content": content}
            try:
                self._candidate(listing)
            except (KeyError, TypeError) as err:
                _LOGGER.warning("Repository copy of %s is dropped: %s", key, err)
                continue
            self._products[key] = listing
        synced_at = data.get(KEY_SYNCED_AT)
        self.synced_at = synced_at if isinstance(synced_at, str) else None
        source = data.get(KEY_SOURCE)
        self.source = None
        if isinstance(source, str) and source:
            try:
                self.source = RepositoryRef.parse(source).spec
            except ValueError as err:
                _LOGGER.warning("Repository copy follows the index again: %s", err)
        commit = data.get(KEY_SOURCE_COMMIT)
        self.source_commit = (
            commit if self.source and isinstance(commit, str) and commit else None
        )
        self._loaded = True

    @property
    def ref(self) -> RepositoryRef | None:
        """Return the ref the copy follows, if not the published index."""
        return RepositoryRef.parse(self.source) if self.source else None

    async def async_follow(self, source: RepositoryRef | None) -> None:
        """Say where the copy is refreshed from, and remember it.

        The products listed stay as they are until the next sync brings the
        copy up to the new place; a sync is what callers do next.
        """
        spec = None if source is None else source.spec
        if spec == self.source:
            return
        self.source = spec
        self.source_commit = None
        await self._async_save()

    def candidates(self) -> list[ProductCandidate]:
        """Return every product the copy lists, with content where it has any."""
        return [self._candidate(record) for record in self._products.values()]

    @staticmethod
    def _wanted(record: dict[str, Any]) -> bool:
        """Say whether a record has no content, or content the index moved past."""
        content = record.get("content")
        held_hash = record.get(KEY_HELD_HASH)
        # No held_hash: an older copy hashed the validated form; re-download.
        return (
            content is None
            or not isinstance(held_hash, str)
            or held_hash != record["hash"]
        )

    async def async_sync(self, hass: HomeAssistant) -> str:
        """Bring the copy's index up to the repository's, and its content along.

        Every product the index lists is recorded; products no longer listed
        are dropped. Content is downloaded only for products the copy already
        holds content of and whose hash moved - what this household picked -
        not for the rest of the repository. A file that fails to download
        leaves the copy's version in place. The result is kept in
        ``sync_error`` as well as returned.

        Following a pull request, the listing is the index its CI published,
        and the files are located by the commit it was built from; content is
        downloaded by the same rule.

        Returns:
            An empty string, or why the copy is not what the index lists.
        """
        listed, error = await self._async_listed(hass)
        if error:
            self.sync_error = error
            return error

        records: dict[str, dict[str, Any]] = {}
        for candidate in listed:
            kept = self._products.get(candidate.id)
            listing = self._record(candidate)
            if kept is not None:
                # The index's word on the product, the content already held.
                listing["content"] = kept.get("content")
                listing[KEY_HELD_HASH] = kept.get(KEY_HELD_HASH)
            records[candidate.id] = listing
        self._products = records

        held = [c for c in listed if records[c.id].get("content") is not None]
        failed = await self._async_download(
            hass, [c for c in held if self._wanted(records[c.id])]
        )
        self.synced_at = datetime.now(UTC).isoformat()
        await self._async_save()
        _LOGGER.debug(
            "Repository copy lists %d products, holds %d; %d refreshed, %d failed",
            len(records),
            len(held),
            len(held) - len(failed),
            len(failed),
        )
        self.sync_error = self._failure_message(failed)
        return self.sync_error

    async def _async_listed(
        self, hass: HomeAssistant
    ) -> tuple[list[ProductCandidate], str]:
        """List the place the copy follows: the published index, or a pull request.

        Returns:
            The candidates and an empty string, or nothing and why.
        """
        if (ref := self.ref) is None:
            return await async_fetch_index(hass)
        try:
            contents = await async_read_ref(hass, ref)
        except RefError as err:
            _LOGGER.debug("Profile repository not read from %s: %s", ref, err)
            return [], str(err)
        self.source_commit = contents.commit
        return contents.products, ""

    async def async_fetch(
        self, hass: HomeAssistant, candidate: ProductCandidate
    ) -> ProductCandidate:
        """Return the picked candidate with the content the index promises.

        Content the copy holds at the promised hash is returned as it is, with
        no request. Otherwise - none held, or held from before the hash moved -
        the one file is downloaded and kept. A download that fails leaves
        content already held to be used, and is raised when there is none.

        Raises:
            CatalogError: Nothing is held and the file could not be fetched, or
                is not a profile.
        """
        record = self._products.get(candidate.id)
        if record is None or not self._wanted(record):
            if candidate.content is not None:
                return candidate
            fetched = await async_download_candidate(hass, candidate)
        else:
            try:
                fetched = await async_download_candidate(hass, self._listed(record))
            except CatalogError as err:
                if candidate.content is None:
                    raise
                _LOGGER.warning(
                    "Profile %s stays at its held version: %s", candidate.id, err
                )
                return candidate
        # Looked up again: a sync that ran during the download may have
        # replaced the record, and the one held before it would be written
        # into nothing.
        if (record := self._products.get(candidate.id)) is not None:
            record["content"] = fetched.content
            record[KEY_HELD_HASH] = fetched.hash
            await self._async_save()
        return fetched

    async def _async_download(
        self, hass: HomeAssistant, wanted: list[ProductCandidate]
    ) -> list[str]:
        """Download these listed products into their records, a few at a time.

        Returns:
            One line per product that could not be downloaded.
        """
        if not wanted:
            return []
        limit = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        async def _download(
            candidate: ProductCandidate,
        ) -> ProductCandidate | CatalogError:
            async with limit:
                try:
                    return await async_download_candidate(
                        hass, candidate, SYNC_FETCH_TIMEOUT
                    )
                except CatalogError as err:
                    return err

        results = await asyncio.gather(*(_download(c) for c in wanted))
        failed: list[str] = []
        for candidate, result in zip(wanted, results, strict=True):
            if isinstance(result, ProductCandidate):
                record = self._products[candidate.id]
                record["content"] = result.content
                record[KEY_HELD_HASH] = result.hash
            else:
                failed.append(f"{candidate.id}: {result}")
        return failed

    async def _async_save(self) -> None:
        """Write the copy."""
        await self._store.async_save(
            {
                KEY_PRODUCTS: self._products,
                KEY_SYNCED_AT: self.synced_at,
                KEY_SOURCE: self.source,
                KEY_SOURCE_COMMIT: self.source_commit,
            }
        )

    @staticmethod
    def _failure_message(failed: list[str]) -> str:
        """Return what to say about downloads that failed, or nothing."""
        if not failed:
            return ""
        return f"{len(failed)} profiles could not be downloaded: {'; '.join(failed)}"

    @staticmethod
    def _record(candidate: ProductCandidate) -> dict[str, Any]:
        """Return what is kept of a listed candidate: the index's word on it."""
        return {
            "vendor": candidate.vendor,
            "product": candidate.product,
            "product_label": candidate.label,
            "product_labels": candidate.labels,
            "vendor_label": candidate.vendor_label,
            "vendor_labels": candidate.vendor_labels,
            "model": candidate.model,
            "category": candidate.category,
            "path": candidate.location,
            "url": candidate.url,
            "hash": candidate.hash,
            "content": None,
            KEY_HELD_HASH: None,
        }

    @staticmethod
    def _listed(record: dict[str, Any]) -> ProductCandidate:
        """Rebuild the candidate the index listed, without content."""
        return ProductCandidate(
            vendor=record["vendor"],
            product=record["product"],
            label=record["product_label"],
            labels=dict(record.get("product_labels") or {}),
            vendor_label=record.get("vendor_label") or record["vendor"],
            vendor_labels=dict(record.get("vendor_labels") or {}),
            model=str(record["model"]),
            source=SOURCE_REPO,
            hash=record["hash"],
            location=record["path"],
            url=record["url"],
            category=record.get("category", CATEGORY_UNKNOWN),
        )

    @classmethod
    def _candidate(cls, record: dict[str, Any]) -> ProductCandidate:
        """Rebuild a candidate from a record.

        With content, the candidate is that content - its held hash and model -
        even where the index has since moved on; without, it is the listing.

        Raises:
            KeyError, TypeError: The record is not one this version wrote.
        """
        listed = cls._listed(record)
        content = record.get("content")
        if content is None:
            return listed
        held_hash = record.get(KEY_HELD_HASH)
        return ProductCandidate(
            vendor=listed.vendor,
            product=listed.product,
            label=listed.label,
            labels=listed.labels,
            vendor_label=listed.vendor_label,
            vendor_labels=listed.vendor_labels,
            model=str(content["model"]),
            source=SOURCE_REPO,
            hash=held_hash if isinstance(held_hash, str) else listed.hash,
            location=listed.location,
            url=listed.url,
            content=dict(content),
            category=listed.category,
        )


async def async_get_mirror(hass: HomeAssistant) -> RepositoryMirror:
    """Return the shared copy, reading it on first use."""
    data = domain_data(hass)
    if data.mirror is None:
        data.mirror = RepositoryMirror(hass)
    await data.mirror.async_load()
    return data.mirror
