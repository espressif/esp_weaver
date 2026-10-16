# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reading the repository from a pull request on GitHub, before it merges.

A vendor working on a profile wants to add a device from it as every user
will - from the repository list - without waiting for the merge. Nothing is
asked of the vendor beyond opening the pull request: each push runs the
repository's CI, which builds the pull request's ``index.json`` with the same
tool that builds the published one and publishes it on the ``ci-indexes``
branch of the upstream repository, as ``pr-<number>/index.json``. This module
reads that file; the products it lists are downloaded by commit from the
fork, one at a time as they are picked, like the published repository's.

The file is read through the GitHub API, whose answers are never stale, so
that a push shows on the next opening of the list; the API allows an
anonymous caller sixty calls an hour, one per read here. When that is spent
the file is read as a raw file instead, which may lag a few minutes.
"""

from dataclasses import dataclass
import logging
import re
from typing import Final

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant

from .download import DownloadError, async_fetch_text
from .index import GITHUB_RAW, INDEX_FILE, MAX_INDEX_SIZE, ParsedIndex, parse_index
from .product import ProductCandidate
from .schema import ProfileError

_LOGGER = logging.getLogger(__name__)

GITHUB_API: Final = "https://api.github.com"
# The profile repository: the published index is built from its master, its
# pull requests are what is read here, and its CI publishes their indexes.
UPSTREAM_REPO: Final = "espressif/esp-weaver-profiles"
# The branch the indexes are published on, and the file per pull request.
INDEX_BRANCH: Final = "ci-indexes"
# Ask the API for the file itself rather than a JSON envelope around it.
API_RAW_ACCEPT: Final = {"Accept": "application/vnd.github.raw+json"}
# A read happens while the user looks at a form.
INDEX_TIMEOUT: Final = ClientTimeout(total=10)

_PULL_NUMBER: Final = re.compile(r"#?(\d+)")
_PULL_URL: Final = re.compile(
    rf"(?:https?://)?(?:www\.)?github\.com/{re.escape(UPSTREAM_REPO)}"
    r"/pull/(\d+)(?:/.*)?"
)
_QUOTA_SPENT: Final = frozenset({"HTTP 403", "HTTP 429"})


class RefError(Exception):
    """The pull request's index could not be read."""


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """A pull request of the upstream repository to read the repository from."""

    pull: int

    @classmethod
    def parse(cls, text: str) -> "RepositoryRef":
        """Read what the user typed or pasted.

        A number, ``123`` or ``#123``, or the pull request's page URL. A
        branch, a fork, or a pull request of some other repository cannot be
        read - only the upstream's CI publishes indexes - and are refused.

        Raises:
            ValueError: The text is not a pull request of the upstream.
        """
        text = text.strip()
        match = _PULL_NUMBER.fullmatch(text) or _PULL_URL.fullmatch(text)
        if match is None or (pull := int(match.group(1))) < 1:
            raise ValueError(f"{text!r} is not a pull request number")
        return cls(pull=pull)

    @property
    def spec(self) -> str:
        """Return the ref as it is typed: the short form, kept and shown."""
        return f"#{self.pull}"

    @property
    def index_path(self) -> str:
        """Return where the pull request's index sits on the index branch."""
        return f"pr-{self.pull}/{INDEX_FILE}"

    @property
    def api_url(self) -> str:
        """Return the index through the API: fresh, one call of the allowance."""
        return (
            f"{GITHUB_API}/repos/{UPSTREAM_REPO}/contents/{self.index_path}"
            f"?ref={INDEX_BRANCH}"
        )

    @property
    def raw_url(self) -> str:
        """Return the index as a raw file: free, but cached a few minutes."""
        return f"{GITHUB_RAW}/{UPSTREAM_REPO}/{INDEX_BRANCH}/{self.index_path}"

    def __str__(self) -> str:
        """Return the ref as it is typed."""
        return self.spec


@dataclass(frozen=True, slots=True)
class RefContents:
    """What a pull request's index listed when it was read."""

    # The products, located by commit, without content.
    products: list[ProductCandidate]
    # The pull request's head commit the index was built from, in full.
    commit: str
    # The fork the commit is in.
    repo: str


async def _fetch_index(hass: HomeAssistant, ref: RepositoryRef) -> str:
    """Return the pull request's index document.

    Raises:
        RefError: There is none, or it could not be fetched.
    """
    try:
        return await async_fetch_text(
            hass, ref.api_url, MAX_INDEX_SIZE, INDEX_TIMEOUT, API_RAW_ACCEPT
        )
    except DownloadError as err:
        if err.detail not in _QUOTA_SPENT:
            raise RefError(_describe(ref, err)) from err
        _LOGGER.debug("GitHub API allowance spent; %s read as a raw file", ref)
    try:
        return await async_fetch_text(hass, ref.raw_url, MAX_INDEX_SIZE, INDEX_TIMEOUT)
    except DownloadError as err:
        raise RefError(_describe(ref, err)) from err


def _describe(ref: RepositoryRef, err: DownloadError) -> str:
    """Return what to tell the user about a failed fetch."""
    if err.detail == "HTTP 404":
        return (
            f"{ref} has no index on GitHub yet: its checks have not finished, "
            "or it changes nothing under vendors/"
        )
    return f"{ref}: {err}"


async def async_read_ref(hass: HomeAssistant, ref: RepositoryRef) -> RefContents:
    """List the products of a pull request, from the index its CI published.

    Raises:
        RefError: The index is not there, could not be read, is not of this
            pull request, or says that CI refused the files - in CI's words,
            which are the tool's.
    """
    text = await _fetch_index(hass, ref)
    try:
        parsed: ParsedIndex = await hass.async_add_executor_job(parse_index, text, None)
    except ProfileError as err:
        raise RefError(f"{ref}: {err}") from err
    if parsed.source is None or parsed.source.pull != ref.pull:
        raise RefError(f"{ref}: the index published for it is of another source")
    if parsed.error is not None:
        raise RefError(
            f"{ref} at {parsed.source.commit[:7]} was refused by its checks: "
            f"{parsed.error}"
        )
    return RefContents(parsed.products, parsed.source.commit, parsed.source.repo)
