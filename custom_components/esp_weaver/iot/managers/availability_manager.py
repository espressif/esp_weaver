# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device availability management for ESP IoT integration."""

import asyncio
import contextlib
from dataclasses import dataclass
import logging
import socket
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from ..discovery.network import DEVICE_SERVICE_TYPE, MDNS_SERVICE_TYPE, PROTO
from ..specs.device_specs import DEFAULT_PORT

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

# mDNS detection timeout
MDNS_DETECTION_TIMEOUT = 3.0

# TCP connectivity check settings
TCP_CHECK_TIMEOUT = 1.0
TCP_MAX_RETRIES = 15
TCP_RETRY_INTERVAL = 2.0


@dataclass
class MDNSDetectionResult:
    """Result of mDNS device detection.

    Attributes:
        detected: Whether device was detected via mDNS
        ip_address: Detected IP address (may differ from expected)
    """

    detected: bool = False
    ip_address: str | None = None


class AvailabilityManager:
    """Manages device availability status."""

    def __init__(
        self,
        hass: HomeAssistant,
        registry: "DeviceRegistry",
        default_port: int = DEFAULT_PORT,
    ) -> None:
        """Initialize the availability manager.

        Args:
            hass: Home Assistant instance
            registry: Device registry for state management
            default_port: Default port for ESP devices
        """
        self.hass = hass
        self.registry = registry
        self.default_port = default_port

    # TCP Connectivity

    async def check_tcp_port_ready(
        self,
        host: str,
        port: int,
        timeout: float = TCP_CHECK_TIMEOUT,
    ) -> bool:
        """Check if TCP port is accepting connections.

        Verifies the HTTP server is ready, not just mDNS broadcasting.
        Prevents race condition where mDNS advertises before HTTP ready.

        Args:
            host: IP address or hostname
            port: TCP port to check
            timeout: Connection timeout in seconds

        Returns:
            True if port accepts connections, False otherwise
        """
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
        except (TimeoutError, ConnectionRefusedError, OSError, ConnectionError):
            # TimeoutError: connection timeout
            # ConnectionRefusedError: port not accepting connections
            # OSError: network-level errors
            # ConnectionError: general connection failures
            return False
        # Connection succeeded
        return True

    async def verify_tcp_connectivity(
        self,
        host: str,
        port: int,
        max_retries: int = TCP_MAX_RETRIES,
        retry_interval: float = TCP_RETRY_INTERVAL,
        node_id: str | None = None,
    ) -> bool:
        """Verify TCP connectivity with retries.

        ESP devices need time after restart to boot and start HTTP server.
        mDNS service starts quickly (~2s) but HTTP server needs ~30s.

        Args:
            host: IP address or hostname
            port: TCP port to check
            max_retries: Maximum number of retry attempts
            retry_interval: Seconds between retries
            node_id: Optional device node ID for early termination check

        Returns:
            True if TCP port becomes ready, False otherwise
        """
        for retry_count in range(max_retries):
            # Check if device already connected via other path
            if node_id and self.registry.is_device_available(node_id):
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

    # mDNS Detection

    async def _async_service_update_callback(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
        service_name: str,
        result: MDNSDetectionResult,
        detected_event: asyncio.Event,
    ) -> None:
        """Handle mDNS service state changes asynchronously.

        Args:
            zeroconf: Zeroconf instance
            service_type: Service type string
            name: Service name
            state_change: Type of state change
            service_name: Expected service name to match
            result: Result object to populate
            detected_event: Event to signal detection
        """
        if name.lower() != service_name.lower():
            return

        if state_change == ServiceStateChange.Removed:
            return

        # Use async service info to avoid blocking
        try:
            info = AsyncServiceInfo(service_type, name)
            if await info.async_request(zeroconf, 3000):
                # ESP local ctrl only supports IPv4, filter out IPv6 addresses
                ipv4_addr = next(
                    (addr for addr in info.addresses if len(addr) == 4), None
                )
                if ipv4_addr:
                    result.ip_address = socket.inet_ntoa(ipv4_addr)
                    result.detected = True
                    detected_event.set()
        except (OSError, TimeoutError) as err:
            # OSError: network-level errors during service info retrieval
            # TimeoutError: mDNS query timeout
            _LOGGER.debug("Error getting service info for %s: %s", name, err)

    async def _wait_for_mdns_detection(
        self,
        detected_event: asyncio.Event,
        timeout: float = MDNS_DETECTION_TIMEOUT,
    ) -> bool:
        """Wait for mDNS detection with timeout.

        Args:
            detected_event: Event that signals detection
            timeout: Detection timeout in seconds

        Returns:
            True if detected within timeout, False otherwise
        """
        try:
            await asyncio.wait_for(detected_event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    def _update_device_ip_if_changed(
        self,
        node_id: str,
        detected_ip: str,
        expected_ip: str | None,
    ) -> bool:
        """Update device IP in registry if it changed.

        Args:
            node_id: Device node ID
            detected_ip: Detected IP address
            expected_ip: Expected IP address (may be None)

        Returns:
            True if IP was actually updated, False otherwise
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
                device.ip = detected_ip
                return True
            _LOGGER.debug("Device %s not found in registry, cannot update IP", node_id)
            return False
        return False

    async def check_device_mdns_available(
        self,
        node_id: str,
        expected_ip: str | None = None,
    ) -> bool:
        """Check if device's mDNS service is broadcasting (device is online).

        Creates a dedicated ServiceBrowser to detect this specific device via mDNS.
        ESP devices broadcast mDNS on startup, then only respond to queries.
        ServiceBrowser automatically sends queries, triggering device to respond.

        Args:
            node_id: Device node ID to check
            expected_ip: Optional expected IP address to verify

        Returns:
            True if device mDNS detected and TCP ready, False otherwise
        """
        service_name = f"{node_id.upper()}.{DEVICE_SERVICE_TYPE}.{PROTO}.local."
        detected_event = asyncio.Event()
        result = MDNSDetectionResult()
        browser: AsyncServiceBrowser | None = None

        try:
            # Delayed import to avoid circular dependency
            from homeassistant.components import zeroconf as zc  # noqa: PLC0415

            aiozc = await zc.async_get_async_instance(self.hass)

            def on_service_state_change(
                zeroconf: Zeroconf,
                service_type: str,
                name: str,
                state_change: ServiceStateChange,
            ) -> None:
                """Handle mDNS service state changes by scheduling async work."""
                self.hass.loop.call_soon_threadsafe(
                    self.hass.async_create_task,
                    self._async_service_update_callback(
                        zeroconf,
                        service_type,
                        name,
                        state_change,
                        service_name,
                        result,
                        detected_event,
                    ),
                )

            browser = AsyncServiceBrowser(
                aiozc.zeroconf,
                MDNS_SERVICE_TYPE,
                handlers=[on_service_state_change],
            )

            try:
                # Wait for mDNS detection
                detected = await self._wait_for_mdns_detection(detected_event)

                if not detected:
                    return False

                # Check if IP changed
                if result.ip_address:
                    self._update_device_ip_if_changed(
                        node_id, result.ip_address, expected_ip
                    )

                # Determine IP to use
                device_ip = result.ip_address
                device = self.registry.get_device(node_id)
                if not device_ip:
                    if device:
                        device_ip = device.ip

                if not device_ip:
                    _LOGGER.warning("No IP address available for device %s", node_id)
                    return False

                # Get device port (use default if port is falsy - None or 0)
                port = device.port if device and device.port else self.default_port

                # Verify TCP connectivity (with early exit if device already available)
                tcp_ready = await self.verify_tcp_connectivity(
                    device_ip, port, node_id=node_id
                )

                if not tcp_ready:
                    _LOGGER.warning(
                        "Device %s mDNS detected but TCP port not ready",
                        node_id,
                    )
                    return False

                # Mark success
                mdns_success = True

            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                # Re-raise CancelledError so task cancellation propagates correctly
                raise
            except (OSError, TimeoutError) as err:
                # OSError: network-level errors
                # TimeoutError: mDNS detection timeout
                _LOGGER.debug(
                    "Error during mDNS detection for device %s: %s",
                    node_id,
                    err,
                )
                return False
            else:
                return mdns_success
            finally:
                # Always cancel browser to clean up resources
                if browser:
                    with contextlib.suppress(Exception):
                        await browser.async_cancel()

        except (ImportError, OSError, RuntimeError) as err:
            # ImportError: zeroconf not available
            # OSError: network-level errors
            # RuntimeError: event loop issues
            _LOGGER.debug(
                "Error checking mDNS availability for device %s: %s",
                node_id,
                err,
            )
            return False
