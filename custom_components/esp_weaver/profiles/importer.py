# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Gets a profile into <config>/esp_weaver/ without a shell.

:func:`async_import_profile` takes a pasted link, :func:`async_save_profile`
takes an uploaded file. Both validate first and write whole or not at all.
"""

from contextlib import suppress
import logging
import os
from pathlib import Path
import re
from typing import Final
from urllib.parse import unquote, urlparse

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..identity import DOMAIN
from .download import MAX_PROFILE_SIZE, DownloadError, Failure, async_fetch_text
from .index import GITHUB_RAW
from .loader import CUSTOM_PROFILE_DIR, YAML_SUFFIXES, parse_profile_text
from .schema import ProfileError

_LOGGER = logging.getLogger(__name__)

TIMEOUT: Final[ClientTimeout] = ClientTimeout(total=30)

# A GitHub file page serves HTML, and pasting one is the mistake everyone makes
# first. Same rewrite as the blueprint importer, for the same reason.
GITHUB_FILE: Final[re.Pattern[str]] = re.compile(
    r"^https://github\.com/(?P<repository>.+)/blob/(?P<path>.+)$"
)
GIST: Final[re.Pattern[str]] = re.compile(
    r"^https://gist\.github\.com/[^/]+/(?P<id>[0-9a-f]+)$"
)

# The name becomes a path under the configuration directory: no separators,
# parent references or control characters; no leading dot, which would hide
# the file and collide with _place's temporary file.
#
# Everything else is allowed: "acme (1).yaml" and non-English names are real.
UNSAFE_NAME: Final[re.Pattern[str]] = re.compile(r"[/\\\x00-\x1f]|\.\.|^\.|^~")

# Which "not a usable profile" message fits: a pasted link or an uploaded file.
INVALID_DOWNLOAD: Final[str] = "import_invalid"
INVALID_UPLOAD: Final[str] = "upload_invalid"


def raw_url(url: str) -> str:
    """Return the URL that serves the file itself. Async friendly."""
    if match := GITHUB_FILE.match(url):
        repository, path = match.group("repository"), match.group("path")
        return f"{GITHUB_RAW}/{repository}/{path}"
    if GIST.match(url):
        return f"{url}/raw"
    return url


def _derived_name(url: str) -> str:
    """Return the filename a URL suggests, empty if it suggests none.

    A gist's raw form ends in ``/raw`` and is given a name of its own.
    """
    if match := GIST.match(url):
        return f"gist_{match.group('id')}.yaml"

    # Decoded, so "%20" saves as a space; _require_safe_name then refuses
    # whatever %2F or %2E%2E decoded to.
    name = unquote(Path(urlparse(raw_url(url)).path).name).strip()
    return name if Path(name).suffix in YAML_SUFFIXES else ""


def _require_safe_name(name: str, shown: str) -> str:
    """Return the name, refusing anything but a plain YAML filename.

    Args:
        shown: What to name in the error when the candidate is empty.

    Raises:
        ServiceValidationError: The name is not one this may write.
    """
    if (
        not name
        or UNSAFE_NAME.search(name)
        or Path(name).suffix not in YAML_SUFFIXES
        # A name that is nothing but a suffix has no stem to identify it by.
        or not Path(name).stem
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="import_bad_filename",
            translation_placeholders={"filename": name or shown},
        )
    return name


def _target_name(url: str, override: str | None) -> str:
    """Work out the filename to write, refusing anything but a plain name."""
    name = (override or "").strip() or _derived_name(url)
    return _require_safe_name(name, url)


# Which message each download failure gets, and whether it is the link
# (ServiceValidationError) or the network (HomeAssistantError) at fault.
_DOWNLOAD_FAILURES: Final[dict[Failure, tuple[str, type[HomeAssistantError]]]] = {
    "scheme": ("import_download_failed", ServiceValidationError),
    "status": ("import_download_failed", ServiceValidationError),
    "network": ("import_download_failed", HomeAssistantError),
    "too_large": ("import_too_large", ServiceValidationError),
    "not_text": ("import_not_text", ServiceValidationError),
}


async def _fetch(hass: HomeAssistant, url: str) -> str:
    """Download the profile, refusing anything oversized."""
    try:
        return await async_fetch_text(hass, url, MAX_PROFILE_SIZE, TIMEOUT)
    except DownloadError as err:
        key, exception = _DOWNLOAD_FAILURES[err.kind]
        raise exception(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={
                "url": url,
                "error": err.detail,
                "limit": str(MAX_PROFILE_SIZE // 1024),
            },
        ) from err


def _write(
    directory: Path, name: str, content: str, overwrite: bool, invalid_key: str
) -> Path:
    """Validate then write the profile. Blocking."""
    # Validate before anything touches the disk: a rejected profile that landed
    # anyway would raise a repair on every load until someone deleted it.
    try:
        parse_profile_text(content, name)
    except ProfileError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=invalid_key,
            translation_placeholders={"filename": name, "detail": str(err)},
        ) from err

    path = directory / name

    def _write_failed(err: OSError) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="import_write_failed",
            translation_placeholders={"filename": name, "error": str(err)},
        )

    # Apart from the write: a *file* called esp_weaver raises FileExistsError
    # here too, and that is not "the profile is already there".
    try:
        directory.mkdir(exist_ok=True)
    except OSError as err:
        raise _write_failed(err) from err

    try:
        _place(path, content, overwrite)
    except FileExistsError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="import_exists",
            translation_placeholders={"filename": name},
        ) from err
    except OSError as err:
        raise _write_failed(err) from err

    return path


def _place(path: Path, content: str, overwrite: bool) -> None:
    """Put the content at path, whole or not at all. Blocking.

    Written beside the target and renamed, so a crash mid-write cannot leave a
    truncated profile that the user - who has no shell - could not delete.

    Raises:
        FileExistsError: The file exists and ``overwrite`` is False. Asked of
            the filesystem, so two racing imports cannot both write.
        OSError: The write failed.
    """
    if not overwrite:
        # Reserves the name, and fails if anyone else holds it. Removed again
        # below, because the rename is what actually puts the content there.
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Best effort: an unwritable directory is one way to get here.
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        if not overwrite:
            with suppress(OSError):
                path.unlink(missing_ok=True)
        raise


async def async_save_profile(
    hass: HomeAssistant,
    filename: str,
    content: str,
    overwrite: bool = False,
    *,
    invalid_key: str = INVALID_UPLOAD,
) -> Path:
    """Put a profile into <config>/esp_weaver/ and return where it landed.

    Validated before anything is written.

    Args:
        filename: Plain ``.yaml``/``.yml`` filename; anything path-like is refused.
        overwrite: Whether to replace a file of that name.
        invalid_key: Translation key to raise when the profile is rejected;
            defaults to the upload wording.

    Raises:
        ServiceValidationError: The name or the profile is not usable.
        HomeAssistantError: The file could not be written.
    """
    name = _require_safe_name(filename.strip(), filename)
    directory = Path(hass.config.path(CUSTOM_PROFILE_DIR))
    return await hass.async_add_executor_job(
        _write, directory, name, content, overwrite, invalid_key
    )


async def async_import_profile(
    hass: HomeAssistant,
    url: str,
    filename: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Download a profile into <config>/esp_weaver/ and return where it landed.

    Raises:
        ServiceValidationError: The link, the download or the profile is not usable.
        HomeAssistantError: The file could not be written.
    """
    # Named from the link as pasted, since that is the form that says what it
    # is: a gist's raw URL ends in /raw and names nothing.
    name = _target_name(url, filename)
    resolved = raw_url(url)
    content = await _fetch(hass, resolved)

    path = await async_save_profile(
        hass, name, content, overwrite, invalid_key=INVALID_DOWNLOAD
    )
    _LOGGER.info("Imported profile %s from %s", path, resolved)
    return path
