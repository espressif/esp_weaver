# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Security and connection management for ESP devices."""

import asyncio
import contextlib
import json
import logging
from typing import Any

from ..local_ctrl import local_ctrl

_LOGGER = logging.getLogger(__name__)

# Connection timeouts
POP_TEST_DELAY = 0.5  # Reduced from 1.0s - minimal delay needed before connection
POP_TEST_DISCONNECT_DELAY = (
    0.5  # Reduced from 10s to 0.5s - brief delay for ESP to release resources
)


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
        """Detect device security configuration.

        Args:
            ip: Device IP address
            port: Device port

        Returns:
            Dictionary with security_version and pop_required keys
        """
        cache_key = (ip, port)
        if cache_key in self._cached_security_info:
            return self._cached_security_info[cache_key]

        try:
            # ESP Local Control only supports HTTP transport in this integration.
            # Even when devices listen on 443, the transport implementation is HTTP.
            transport_type = "http"
            service_name = f"{ip}:{port}"

            transport = await self._local_ctrl.get_transport(
                transport_type, service_name
            )

            if not transport:
                _LOGGER.warning("Failed to get transport for %s", service_name)
                return {"security_version": 1, "pop_required": True}

            # Default to security mode 1 with PoP
            security_version = 1
            pop_required = True

            try:
                version_response = transport.send_data("esp_local_ctrl/version", "none")
                if asyncio.iscoroutine(version_response):
                    version_response = await version_response
                version_info = None
                try:
                    version_info = json.loads(version_response)
                except (json.JSONDecodeError, TypeError) as e:
                    _LOGGER.warning(
                        "Failed to parse version response from %s: %s",
                        service_name,
                        e,
                    )

                # Method 1: Device directly reports sec_ver (preferred)
                if (
                    isinstance(version_info, dict)
                    and "local_ctrl" in version_info
                    and "sec_ver" in version_info["local_ctrl"]
                ):
                    security_version = version_info["local_ctrl"]["sec_ver"]

                    if security_version == 0:
                        pop_required = False
                    else:
                        has_no_pop = await self._local_ctrl.has_capability(
                            transport, "no_pop"
                        )
                        pop_required = not has_no_pop

                # Method 2: Query capabilities (devices without sec_ver field)
                else:
                    has_no_sec = await self._local_ctrl.has_capability(
                        transport, "no_sec"
                    )
                    has_no_pop = await self._local_ctrl.has_capability(
                        transport, "no_pop"
                    )

                    if has_no_sec:
                        security_version = 0
                        pop_required = False
                    else:
                        security_version = 1
                        pop_required = not has_no_pop

                _LOGGER.info(
                    "Detected security for %s: sec%s, pop=%s",
                    service_name,
                    security_version,
                    pop_required,
                )

                self._cached_security_info[cache_key] = {
                    "security_version": security_version,
                    "pop_required": pop_required,
                }

                return self._cached_security_info[cache_key]

            except (OSError, RuntimeError) as err:
                _LOGGER.error("Error querying security for %s: %s", service_name, err)
                return {"security_version": 1, "pop_required": True}
            finally:
                if hasattr(transport, "close"):
                    with contextlib.suppress(Exception):
                        close_result = transport.close()
                        if asyncio.iscoroutine(close_result):
                            await close_result

        except (OSError, TimeoutError, ConnectionError) as err:
            _LOGGER.error("Failed to detect security for %s:%s: %s", ip, port, err)
            return {"security_version": 1, "pop_required": True}

    async def test_pop_connection(self, ip: str, pop: str, port: int) -> bool:
        """Test if the provided PoP works for connecting to the device.

        Args:
            ip: Device IP address
            pop: Proof of Possession string to test
            port: Device port

        Returns:
            True if PoP is valid, False otherwise
        """
        try:
            await asyncio.sleep(POP_TEST_DELAY)

            cache_key = (ip, port)
            if cache_key not in self._cached_security_info:
                raise ValueError(
                    f"Security info not cached for {ip}:{port}. "
                    "Call detect_device_security first."
                )
            security_info = self._cached_security_info[cache_key]
            # ESP Local Control only supports HTTP transport in this integration.
            transport_type = "http"
            secver = security_info["security_version"]

            transport = None
            try:
                service_name = f"{ip}:{port}"
                transport = await self._local_ctrl.get_transport(
                    sel_transport=transport_type,
                    service_name=service_name,
                )

                if not transport:
                    return False

                security_ctx = self._local_ctrl.get_security(
                    secver=secver,
                    sec_patch_ver=None,
                    username=None,
                    password=None,
                    pop=pop or "",
                )

                if not security_ctx:
                    return False

                session_ok = await self._local_ctrl.establish_session(
                    transport, security_ctx
                )

                return bool(session_ok)

            finally:
                if transport:
                    if hasattr(transport, "close"):
                        with contextlib.suppress(Exception):
                            close_result = transport.close()
                            if asyncio.iscoroutine(close_result):
                                await close_result
                    # Delay after transport was used to let device release resources
                    await asyncio.sleep(POP_TEST_DISCONNECT_DELAY)

        except (OSError, TimeoutError, ConnectionError) as err:
            _LOGGER.debug("PoP connection test failed for %s:%s: %s", ip, port, err)
            return False
