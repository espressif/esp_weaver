# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Security and connection management for ESP devices."""

import asyncio
import contextlib
import logging
from typing import Any, Final

from ..local_ctrl import local_ctrl
from ..local_ctrl.transport import Transport
from ..specs.keys import KEY_POP_REQUIRED, KEY_SECURITY_VERSION

_LOGGER = logging.getLogger(__name__)

# Settling time either side of the throwaway connection a PoP test makes.
POP_TEST_DELAY: Final[float] = 0.5
POP_TEST_DISCONNECT_DELAY: Final[float] = 0.5


def _assume_secured() -> dict[str, Any]:
    """Return what to assume about a device that would not say: sec1 with a PoP."""
    return {KEY_SECURITY_VERSION: 1, KEY_POP_REQUIRED: True}


def _close_transport(transport: Transport) -> None:
    """Close a throwaway transport, whatever state the device left it in."""
    with contextlib.suppress(OSError, RuntimeError):
        transport.close()


class ESPSecurityManager:
    """Handle device security detection and PoP validation."""

    def __init__(self, local_ctrl_module: Any = None) -> None:
        """Initialize security manager."""
        self._local_ctrl = local_ctrl_module or local_ctrl
        self._cached_security_info: dict[tuple[str, int], dict[str, Any]] = {}

    def clear_cache(self) -> None:
        """Clear cached security information."""
        self._cached_security_info.clear()

    async def detect_device_security(self, ip: str, port: int) -> dict[str, Any]:
        """Ask a device what security it runs; only a real answer is cached."""
        cache_key = (ip, port)
        if cache_key in self._cached_security_info:
            return self._cached_security_info[cache_key]

        service_name = f"{ip}:{port}"
        try:
            # ESP Local Control only supports HTTP transport in this integration.
            # Even when devices listen on 443, the transport implementation is HTTP.
            transport = await self._local_ctrl.get_transport("http", service_name)
        except OSError as err:
            _LOGGER.error("Failed to detect security for %s: %s", service_name, err)
            return _assume_secured()

        if not transport:
            _LOGGER.warning("Failed to get transport for %s", service_name)
            return _assume_secured()

        try:
            security_version, pop_required = await self._query_security(transport)
        except (OSError, RuntimeError) as err:
            _LOGGER.error("Error querying security for %s: %s", service_name, err)
            return _assume_secured()
        finally:
            _close_transport(transport)

        _LOGGER.info(
            "Detected security for %s: sec%s, pop=%s",
            service_name,
            security_version,
            pop_required,
        )
        self._cached_security_info[cache_key] = {
            KEY_SECURITY_VERSION: security_version,
            KEY_POP_REQUIRED: pop_required,
        }
        return self._cached_security_info[cache_key]

    async def ask_device_security(self, ip: str, port: int) -> int | None:
        """Return the security version the device itself stated, or None.

        Unlike ``detect_device_security`` this does not fall back.
        """
        security_info = await self.detect_device_security(ip, port)
        if (ip, port) not in self._cached_security_info:
            return None
        return int(security_info[KEY_SECURITY_VERSION])

    async def _query_security(self, transport: Transport) -> tuple[int, bool]:
        """Ask a reachable device what security it is running.

        From the unencrypted ``version`` document: ``sec_ver`` where stated,
        else the ``no_sec``/``no_pop`` capabilities; unsaid means sec1 with a PoP.

        Raises:
            RuntimeError: If the device did not answer.
        """
        info = await self._local_ctrl.version_info(transport)
        if info is None:
            raise RuntimeError("no answer to esp_local_ctrl/version")

        capabilities = info.get("cap", ())
        if "sec_ver" in info:
            security_version = info["sec_ver"]
        else:
            security_version = 0 if "no_sec" in capabilities else 1
        pop_required = security_version != 0 and "no_pop" not in capabilities
        return security_version, pop_required

    async def test_pop_connection(self, ip: str, pop: str, port: int) -> bool:
        """Say whether a PoP the user typed will actually open a session."""
        try:
            await asyncio.sleep(POP_TEST_DELAY)

            # A fallback is not cached, so a device that was still starting
            # during detection is asked again here rather than remembered
            # wrongly.
            security_info = await self.detect_device_security(ip, port)
            secver = security_info[KEY_SECURITY_VERSION]

            transport = None
            try:
                # ESP Local Control only supports HTTP transport here.
                transport = await self._local_ctrl.get_transport(
                    sel_transport="http",
                    service_name=f"{ip}:{port}",
                )
                if not transport:
                    return False

                return await self._handshake(transport, secver, pop)

            finally:
                if transport:
                    _close_transport(transport)
                    # Delay after transport was used to let device release resources
                    await asyncio.sleep(POP_TEST_DISCONNECT_DELAY)

        except OSError as err:
            _LOGGER.debug("PoP connection test failed for %s:%s: %s", ip, port, err)
            return False

    async def _handshake(self, transport: Transport, secver: int, pop: str) -> bool:
        """Try one handshake with the same ``build_security`` the runtime uses.

        Returns:
            True if the device accepted the PoP.
        """
        security_ctx = await self._local_ctrl.build_security(transport, secver, pop)
        if not security_ctx:
            return False

        return bool(await self._local_ctrl.establish_session(transport, security_ctx))
