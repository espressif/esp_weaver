# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Fetches one text file from the web, within a size limit."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlparse

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

type Failure = Literal["scheme", "status", "network", "too_large", "not_text"]


@dataclass(slots=True)
class DownloadError(Exception):
    """The download did not produce usable text.

    ``kind`` says which check failed; ``detail`` is the status, error or URL.
    """

    kind: Failure
    detail: str

    def __str__(self) -> str:
        """Return the detail, which is what a log line wants."""
        return self.detail


# The most a profile may weigh; the largest shipped one is around 40 KB.
MAX_PROFILE_SIZE: Final[int] = 512 * 1024


def require_web_url(url: str) -> None:
    """Refuse a URL that is not http or https; ``file://`` would read local files.

    Raises:
        DownloadError: The URL is not http or https.
    """
    if urlparse(url).scheme not in ("http", "https"):
        raise DownloadError("scheme", f"refused a redirect to {url}")


async def async_fetch_text(
    hass: HomeAssistant,
    url: str,
    limit: int,
    timeout: ClientTimeout,
    headers: Mapping[str, str] | None = None,
) -> str:
    """Download ``url`` as UTF-8 text of at most ``limit`` bytes.

    Raises:
        DownloadError: The download failed, was refused, or was not text.
    """
    require_web_url(url)
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=timeout, headers=headers) as response:
            require_web_url(str(response.url))
            # Not raise_for_status, so the status itself reaches the message.
            if response.status != 200:
                raise DownloadError("status", f"HTTP {response.status}")
            body = await response.content.read(limit + 1)
    except (ClientError, TimeoutError) as err:
        raise DownloadError("network", str(err) or type(err).__name__) from err

    if len(body) > limit:
        raise DownloadError("too_large", f"larger than {limit // 1024} KB")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as err:
        raise DownloadError("not_text", "not UTF-8 text") from err
