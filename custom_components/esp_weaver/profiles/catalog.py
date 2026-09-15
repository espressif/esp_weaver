# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Everything a device could be bound to, gathered into one list.

Shipped products, the configuration directory, copies kept from earlier
bindings, and the local copy of the repository - refreshed first when asked to
go online. Everything found is listed; nothing here prefers one copy over
another or preselects anything.
"""

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, cast

from homeassistant.core import HomeAssistant

from ..data import domain_data
from .loader import CUSTOM_PROFILE_DIR, scan_builtin, scan_local, sync_file_issues
from .mirror import CatalogError, async_get_mirror
from .product import (
    SOURCE_BUILTIN,
    SOURCE_LOCAL,
    SOURCE_REPO,
    ProductCandidate,
    ProfileProblem,
)
from .store import async_get_store

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProfileCatalog:
    """What can be offered right now, keyed per copy, and what went wrong."""

    candidates: dict[str, ProductCandidate]
    problems: list[ProfileProblem] = field(default_factory=list)
    # Why the last refresh of the repository copy this run fell short. Empty
    # when it was never asked, or when the copy holds everything listed.
    index_error: str = ""
    # Whether the repository copy was refreshed for this build.
    online: bool = False
    # When the repository copy was last brought up to the index, if ever.
    repository_synced_at: str | None = None
    # The pull request the copy follows instead of the published index, as
    # typed, and the commit it was last read at; None when it follows the
    # index.
    repository_source: str | None = None
    repository_commit: str | None = None

    def get(self, key: str) -> ProductCandidate | None:
        """Return the candidate offered under this key, if any."""
        return self.candidates.get(key)

    def repository(self) -> list[ProductCandidate]:
        """Return what the repository copy holds."""
        return [c for c in self.candidates.values() if c.source == SOURCE_REPO]

    def by_id(self, wanted: str) -> list[ProductCandidate]:
        """Return every copy of the product with this id, in list order."""
        return [c for c in self.sorted() if c.id == wanted]

    def by_hash(
        self, content_hash: str, product: str, source: str
    ) -> ProductCandidate | None:
        """Return this product's copy with this content from this source, if offered."""
        return next(
            (
                c
                for c in self.sorted()
                if c.hash == content_hash and c.id == product and c.source == source
            ),
            None,
        )

    def vendors(self) -> dict[str, int]:
        """Return every vendor with a candidate, and how many lines, in list order."""
        counts: dict[str, int] = {}
        for candidate in self.listed():
            counts[candidate.vendor] = counts.get(candidate.vendor, 0) + 1
        return dict(sorted(counts.items()))

    def listed(self, vendor: str | None = None) -> list[ProductCandidate]:
        """Return the lines of the product list: ``sorted`` less identical copies.

        Copies of one product that read the same and map the same - same
        name, same content hash - are one line: the shipped Espressif profiles
        and the repository's, a file copied into the folder unchanged, the copy
        kept from a binding while its original stands. There is nothing to pick
        between, so the first in list order stands for them all: the user's
        folder, then built-in, then the kept copy, then the repository. Copies
        that differ in either stay apart, since those are a real choice.
        """
        seen: set[tuple[str, str, str]] = set()
        lines: list[ProductCandidate] = []
        for candidate in self.sorted(vendor):
            if (mark := (candidate.id, candidate.hash, candidate.name)) in seen:
                continue
            seen.add(mark)
            lines.append(candidate)
        return lines

    def sorted(self, vendor: str | None = None) -> list[ProductCandidate]:
        """Return the candidates by vendor, category, product, source, location.

        With ``vendor``, only that vendor's.
        """
        return sorted(
            (
                c
                for c in self.candidates.values()
                if vendor is None or c.vendor == vendor
            ),
            key=lambda c: (c.vendor, c.category_rank, c.product, c.rank, c.location),
        )

    def describe(self) -> dict[str, Any]:
        """Return a diagnostics-friendly summary."""
        return {
            "online": self.online,
            "index_error": self.index_error,
            "repository_synced_at": self.repository_synced_at,
            "products": [c.describe() for c in self.sorted()],
            "problems": [
                {"kind": p.kind, "file": str(p.path), "detail": p.detail}
                for p in self.problems
            ],
        }


def _scan_disk(custom_dir: Path) -> tuple[list[ProductCandidate], list[ProfileProblem]]:
    """Read both directories. Blocking."""
    builtin, problems = scan_builtin(SOURCE_BUILTIN)
    local, local_problems = scan_local(custom_dir, SOURCE_LOCAL)
    return [*builtin, *local], [*problems, *local_problems]


async def async_build_catalog(hass: HomeAssistant, *, online: bool) -> ProfileCatalog:
    """Assemble the list from disk, the store and the repository copy.

    With ``online``, the repository copy's index is refreshed first - the
    list then shows the repository as it is, or as it was last seen when the
    repository does not answer. A product's file is downloaded when it is
    picked; see ``async_fetch_content``. The disk scan also refreshes the
    repairs about unreadable files.
    """
    custom_dir = Path(hass.config.path(CUSTOM_PROFILE_DIR))
    on_disk, problems = await hass.async_add_executor_job(_scan_disk, custom_dir)
    store = await async_get_store(hass)
    mirror = await async_get_mirror(hass)
    if online:
        await mirror.async_sync(hass)

    found = [*on_disk, *store.candidates(), *mirror.candidates()]
    candidates = {candidate.key: candidate for candidate in found}

    sync_file_issues(hass, problems)
    catalog = ProfileCatalog(
        candidates=candidates,
        problems=problems,
        index_error=mirror.sync_error,
        online=online,
        repository_synced_at=mirror.synced_at,
        repository_source=mirror.source,
        repository_commit=mirror.source_commit,
    )
    domain_data(hass).catalog = catalog
    _LOGGER.debug(
        "Profile catalog holds %d products (%d from the repository copy%s)",
        len(candidates),
        len(catalog.repository()),
        ", just refreshed" if online and not mirror.sync_error else "",
    )
    return catalog


async def async_get_catalog(hass: HomeAssistant) -> ProfileCatalog:
    """Return the offline catalog, building it on first use, under a lock."""
    data = domain_data(hass)
    if data.catalog is not None:
        return data.catalog

    async with data.catalog_lock:
        # Whoever held the lock before us may have built it meanwhile; the
        # type checker cannot see the await inside the lock, hence the cast.
        if (built := cast(ProfileCatalog | None, data.catalog)) is not None:
            return built
        return await async_build_catalog(hass, online=False)


async def async_refresh_catalog(hass: HomeAssistant, *, online: bool) -> ProfileCatalog:
    """Read everything again, refreshing the repository copy first if asked."""
    async with domain_data(hass).catalog_lock:
        return await async_build_catalog(hass, online=online)


async def async_fetch_content(
    hass: HomeAssistant, candidate: ProductCandidate
) -> ProductCandidate:
    """Return the candidate with its content in hand, downloading if it has none.

    A repository product is fetched when the copy does not hold it at the
    hash the index promises, and kept, so the next pick of it is offline.

    Raises:
        CatalogError: The product could not be fetched or is not a profile.
    """
    if candidate.source == SOURCE_REPO:
        mirror = await async_get_mirror(hass)
        return await mirror.async_fetch(hass, candidate)
    # Every other source is read from a file or the store, content and all.
    if candidate.content is None:
        raise CatalogError(f"{candidate.id} from {candidate.source} has no content")
    return candidate
