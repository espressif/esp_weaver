# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device availability management for ESP IoT integration."""

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from zeroconf.asyncio import AsyncServiceInfo

from ..discovery import MDNS_SERVICE_TYPE, SERVICE_INFO_TIMEOUT_MS, ipv4_of
from ..host import IotHost
from ..specs.device_specs import DEFAULT_PORT

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

# A device that has just restarted is back on mDNS in about two seconds and
# answering HTTP in about thirty, so the wait has to cover the difference.
TCP_CHECK_TIMEOUT: Final[float] = 1.0
TCP_MAX_RETRIES: Final[int] = 15
TCP_RETRY_INTERVAL: Final[float] = 2.0


class AvailabilityManager:
    """Manages device availability status."""

    def __init__(
        self,
        host: IotHost,
        registry: "DeviceRegistry",
        default_port: int = DEFAULT_PORT,
    ) -> None:
        """Initialize the availability manager."""
        self.host = host
        self.registry = registry
        self.default_port = default_port

    async def check_tcp_port_ready(
        self,
        host: str,
        port: int,
        timeout: float = TCP_CHECK_TIMEOUT,
    ) -> bool:
        """Say whether the device's HTTP server is accepting connections."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
        except OSError:
            return False
        return True

    async def verify_tcp_connectivity(
        self,
        host: str,
        port: int,
        max_retries: int = TCP_MAX_RETRIES,
        retry_interval: float = TCP_RETRY_INTERVAL,
        node_id: str | None = None,
    ) -> bool:
        """Wait out a rebooting device, up to about half a minute.

        Back on mDNS in two seconds, answering HTTP after thirty. `node_id`
        lets a device that came back by another path end the wait early.
        """
        for retry_count in range(max_retries):
            if node_id and await self.registry.is_device_available_async(node_id):
                _LOGGER.debug(
                    "Device %s already available, skipping TCP verification",
                    node_id,
                )
                return True

            if await self.check_tcp_port_ready(host, port):
                return True

            if retry_count < max_retries - 1:
                await asyncio.sleep(retry_interval)

        _LOGGER.warning(
            "TCP port %s:%d not ready after %d retries",
            host,
            port,
            max_retries,
        )
        return False

    def _update_device_ip_if_changed(
        self,
        node_id: str,
        detected_ip: str,
        expected_ip: str | None,
    ) -> bool:
        """Follow a device that answered from somewhere other than expected.

        Returns:
            True only if the registry was actually moved to the new address.
        """
        if expected_ip and detected_ip != expected_ip:
            _LOGGER.warning(
                "Device %s IP changed: expected %s, detected %s",
                node_id,
                expected_ip,
                detected_ip,
            )
            device = self.registry.get_device(node_id)
            if device:
                self.registry.register_device(node_id, detected_ip, device.port)
                return True
            _LOGGER.debug("Device %s not found in registry, cannot update IP", node_id)
            return False
        return False

    async def check_device_mdns_available(
        self,
        node_id: str,
        expected_ip: str | None = None,
    ) -> bool:
        """Say whether one device is on the network and ready to be talked to.

        Asks for the device's own record by name, which zeroconf answers from
        cache when it can; browsing the whole service type would have every
        device answer for the sake of one.

        Returns:
            True if the device answered mDNS and its port accepts connections.
        """
        service_name = f"{node_id.upper()}.{MDNS_SERVICE_TYPE}"
        try:
            aiozc = await self.host.async_zeroconf()
            info = AsyncServiceInfo(MDNS_SERVICE_TYPE, service_name)
            answered = await info.async_request(aiozc.zeroconf, SERVICE_INFO_TIMEOUT_MS)
        except (ImportError, OSError, RuntimeError) as err:
            _LOGGER.debug(
                "Error checking mDNS availability for device %s: %s",
                node_id,
                err,
            )
            return False

        if not answered:
            _LOGGER.debug(
                "Device %s did not answer mDNS within %d ms",
                node_id,
                SERVICE_INFO_TIMEOUT_MS,
            )
            return False

        try:
            return await self._verify_detected_device(
                node_id, ipv4_of(info), expected_ip
            )
        except (OSError, RuntimeError) as err:
            _LOGGER.debug(
                "Error verifying device %s after mDNS answered: %s", node_id, err
            )
            return False

    async def _verify_detected_device(
        self,
        node_id: str,
        detected_ip: str | None,
        expected_ip: str | None,
    ) -> bool:
        """Check that a device which answered mDNS also accepts connections.

        A device advertises itself before its HTTP server is listening.

        Args:
            detected_ip: The address the record carried, if any.
            expected_ip: The address the device was last known at, so a move
                can be noticed.
        """
        if detected_ip:
            self._update_device_ip_if_changed(node_id, detected_ip, expected_ip)

        device = self.registry.get_device(node_id)
        device_ip = detected_ip or (device.ip if device else None)
        if not device_ip:
            _LOGGER.warning("No IP address available for device %s", node_id)
            return False

        port = device.port if device else self.default_port

        if not await self.verify_tcp_connectivity(device_ip, port, node_id=node_id):
            _LOGGER.warning(
                "Device %s mDNS detected but TCP port not ready",
                node_id,
            )
            return False

        return True
