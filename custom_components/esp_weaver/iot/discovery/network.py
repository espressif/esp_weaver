# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Network discovery and device detection for ESP devices."""

import asyncio
import contextlib
import logging
import re
import socket
import time
from typing import Any

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

from ..specs.device_specs import DEFAULT_PORT
from ..specs.keys import KEY_DEVICE_NAME, KEY_IP, KEY_NODE_ID, KEY_PORT

_LOGGER = logging.getLogger(__name__)

# Service discovery constants
DEVICE_SERVICE_TYPE = "_esp_local_ctrl"
PROTO = "_tcp"
# Full mDNS service type string for ServiceBrowser
MDNS_SERVICE_TYPE = f"{DEVICE_SERVICE_TYPE}.{PROTO}.local."

# Discovery timeouts (in seconds)
DISCOVERY_TIMEOUT = 20.0
DISCOVERY_SETTLE_TIME = 3.0

# Discovery throttling (seconds)
DISCOVERY_THROTTLE_INTERVAL = 5


def extract_ip_from_service_info(service_info: ServiceInfo | None) -> str | None:
    """Extract IPv4 address from zeroconf service info.

    Note: ESP esp_local_ctrl service only advertises IPv4 (A record).

    Args:
        service_info: Zeroconf ServiceInfo object

    Returns:
        IPv4 address string or None if not available
    """
    if service_info and service_info.addresses:
        # ESP device only advertises IPv4, addresses[0] is always 4 bytes
        addr = service_info.addresses[0]
        if len(addr) == 4:  # IPv4 only
            return socket.inet_ntoa(addr)
    return None


def _is_valid_node_id(node_id_str: str) -> bool:
    """Check if string is a valid node ID format."""
    id_pattern = r"^[0-9A-Za-z_-]{1,64}$"
    return bool(re.match(id_pattern, node_id_str))


def _format_node_id(node_id_str: str) -> str:
    """Format node ID to standard format (alphanumeric with underscore and hyphen)."""
    return re.sub(r"[^0-9A-Za-z_-]", "_", node_id_str)


def _extract_node_id_from_service(
    service_info: ServiceInfo | None,
) -> tuple[str | None, str | None]:
    """Extract node ID and device name from zeroconf service TXT record.

    Args:
        service_info: Zeroconf service info object.

    Returns:
        Tuple of (node_id, device_name). Either may be None.
    """
    if not service_info or not service_info.properties:
        return (None, None)

    device_name = None
    if b"device_name" in service_info.properties:
        device_name_bytes = service_info.properties[b"device_name"]
        if device_name_bytes is not None:
            device_name = device_name_bytes.decode("utf-8", errors="replace")

    # Get node_id from TXT properties
    if b"node_id" in service_info.properties:
        node_id_bytes = service_info.properties[b"node_id"]
        if node_id_bytes is None:
            return (None, device_name)
        id_value = node_id_bytes.decode("utf-8", errors="replace")
        if _is_valid_node_id(id_value):
            return (_format_node_id(id_value), device_name)

    return (None, device_name)


async def async_discover_devices(
    hass: HomeAssistant,
    listener_class: type["BaseMDNSListener"] | None = None,
) -> list[dict[str, Any]]:
    """Discover ESP devices via mDNS/Zeroconf.

    Args:
        hass: Home Assistant instance
        listener_class: Listener class extending BaseMDNSListener.
                       Defaults to ESPDeviceListener if not provided.

    Returns:
        List of discovered device dictionaries
    """
    # Use ESPDeviceListener as default if no listener_class provided
    if listener_class is None:
        actual_listener_class: type[BaseMDNSListener] = ESPDeviceListener
    else:
        actual_listener_class = listener_class

    try:
        zc = await zeroconf.async_get_instance(hass)
        listener = actual_listener_class(hass)
        listener.reset()

        browser = ServiceBrowser(zc, MDNS_SERVICE_TYPE, listener)

        try:
            await asyncio.wait_for(
                listener.discovered_event.wait(), timeout=DISCOVERY_TIMEOUT
            )
            await asyncio.sleep(DISCOVERY_SETTLE_TIME)
            return listener.devices.copy()

        except TimeoutError:
            _LOGGER.debug(
                "Device discovery timed out after %s seconds, returning %d device(s)",
                DISCOVERY_TIMEOUT,
                len(listener.devices),
            )
            return listener.devices.copy()
        finally:
            if browser and hasattr(browser, "cancel"):
                with contextlib.suppress(Exception):
                    browser.cancel()
            listener.reset()

    except OSError as err:
        _LOGGER.error("Network error during device discovery: %s", err)
        return []


class BaseMDNSListener(ServiceListener):
    """Base class for mDNS listeners with common service handling logic.

    Inherits from ServiceListener to satisfy zeroconf ServiceBrowser type requirements.
    Provides shared implementation for zeroconf service callbacks and
    async service info retrieval. Subclasses implement _async_process_service
    for specific handling logic.

    Attributes:
        hass: Home Assistant instance.
        devices: List of discovered device dictionaries.
        discovered_event: Event signaling device discovery.
    """

    # Subclass interface - must be initialized by subclasses
    devices: list[dict[str, Any]]
    discovered_event: asyncio.Event

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the base listener.

        Args:
            hass: Home Assistant instance.
        """
        self.hass = hass
        self.devices = []
        self.discovered_event = asyncio.Event()

    def reset(self) -> None:
        """Reset the listener state. Override in subclass if needed."""
        self.devices = []
        self.discovered_event.clear()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service addition."""
        self._schedule_service_handling(type_, name, "added")

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service update."""
        self._schedule_service_handling(type_, name, "updated")

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service removal. Override in subclass if needed."""

    def _schedule_service_handling(
        self, type_: str, name: str, change_type: str
    ) -> None:
        """Schedule async service handling to avoid blocking zeroconf thread."""
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task,
            self._async_handle_service_change(type_, name, change_type),
        )

    async def _async_handle_service_change(
        self, type_: str, name: str, change_type: str
    ) -> None:
        """Common handler for all service changes (async)."""
        try:
            # Use async service info to avoid blocking
            info = AsyncServiceInfo(type_, name)
            zc = await zeroconf.async_get_instance(self.hass)
            if not await info.async_request(zc, 3000):
                return

            if not info.addresses:
                return

            node_id, device_name = _extract_node_id_from_service(info)
            if not node_id:
                return

            ip = extract_ip_from_service_info(info)
            if ip is None:
                _LOGGER.debug("No IP address found for service %s, skipping", name)
                return
            port = info.port or DEFAULT_PORT

            await self._async_process_service(
                node_id, ip, port, device_name, change_type, name
            )

        except (AttributeError, KeyError, ValueError, OSError) as err:
            _LOGGER.debug(
                "Error handling service %s for %s: %s", change_type, name, err
            )

    async def _async_process_service(
        self,
        node_id: str,
        ip: str,
        port: int,
        device_name: str | None,
        change_type: str,
        service_name: str,
    ) -> None:
        """Process service data. Override in subclass.

        Args:
            node_id: Device node ID.
            ip: Device IP address.
            port: Device port.
            device_name: Device name from mDNS TXT record.
            change_type: Type of change ("added", "updated", "removed").
            service_name: Original service name for logging.
        """


class GlobalMDNSListener(BaseMDNSListener):
    """Lightweight mDNS listener for global device monitoring.

    This listener is used by ESPWeaverApi to monitor already-configured devices.
    It only processes devices that have existing config entries and delegates
    updates to the API.

    Attributes:
        hass: Home Assistant instance.
        api: API instance for device management.
    """

    def __init__(self, hass: HomeAssistant, api: Any) -> None:
        """Initialize the global listener.

        Args:
            hass: Home Assistant instance.
            api: API instance for device management (required).
        """
        super().__init__(hass)
        self.api = api

    async def _async_process_service(
        self,
        node_id: str,
        ip: str,
        port: int,
        device_name: str | None,
        change_type: str,
        service_name: str,
    ) -> None:
        """Process service change for configured devices only."""
        # Only process add/update events
        if change_type == "removed":
            return

        # Only process devices with existing config entries
        existing_entries = self.hass.config_entries.async_entries(self.api.domain)
        existing_node_ids = {
            entry.unique_id for entry in existing_entries if entry.unique_id
        }

        if node_id not in existing_node_ids:
            _LOGGER.debug(
                "Device %s not in configured entries, skipping update", node_id
            )
            return

        _LOGGER.debug(
            "mDNS %s event for configured device %s at %s:%s",
            change_type,
            node_id,
            ip,
            port,
        )

        # Check if device already has an active connection
        # If connected, only update IP/port metadata without triggering reconnection
        # This prevents mDNS announcements from disrupting active data streams
        client = self.api.registry.get_client(node_id)
        if client:
            # Update device IP/port in registry without connection check
            device = self.api.registry.get_device(node_id)
            if device:
                old_ip, old_port = device.ip, device.port
                if old_ip != ip or old_port != port:
                    device.ip = ip
                    device.port = port
                    _LOGGER.info(
                        "Updated IP/port for connected device %s: %s:%s -> %s:%s",
                        node_id,
                        old_ip,
                        old_port,
                        ip,
                        port,
                    )
                else:
                    _LOGGER.debug(
                        "Device %s already connected at %s:%s, skipping update",
                        node_id,
                        ip,
                        port,
                    )
                return

        # No active connection - proceed with full update/reconnection
        try:
            await self.api.update_device(node_id, ip, port)
            _LOGGER.info(
                "Successfully updated device %s via mDNS at %s:%s",
                node_id,
                ip,
                port,
            )
        except (OSError, ConnectionError, TimeoutError) as err:
            # Network errors during device update are expected and recoverable
            _LOGGER.warning(
                "Failed to update device %s at %s:%s: %s",
                node_id,
                ip,
                port,
                err,
            )


class ESPDeviceListener(BaseMDNSListener):
    """Listener for ESP device discovery via zeroconf (config flow mode).

    This class is used during config flow to discover all ESP devices
    on the network. It maintains a list of discovered devices and throttles
    rapid discovery events to prevent flooding.

    Attributes:
        hass: Home Assistant instance.
        devices: List of discovered device information dictionaries.
        seen_node_ids: Set of node IDs that have been discovered.
        discovered_event: Event that is set when a new device is discovered.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the device listener.

        Args:
            hass: Home Assistant instance.
        """
        super().__init__(hass)
        self.devices: list[dict[str, Any]] = []
        self.seen_node_ids: set[str] = set()
        self.discovered_event: asyncio.Event = asyncio.Event()
        # Per-device throttling to prevent rapid updates for the same device
        # while allowing updates for different devices
        self._last_discovery_times: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    def reset(self) -> None:
        """Reset the listener state.

        Note: This method is not thread-safe with discovered_event. It should
        only be called when no ServiceBrowser is actively running (before
        starting or after cancelling the browser).
        """
        self.devices.clear()
        self.seen_node_ids.clear()
        self.discovered_event.clear()
        self._last_discovery_times.clear()

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle removal of a service."""
        self._schedule_service_handling(type_, name, "removed")

    async def _async_process_service(
        self,
        node_id: str,
        ip: str,
        port: int,
        device_name: str | None,
        change_type: str,
        service_name: str,
    ) -> None:
        """Process discovered or removed service."""
        if change_type == "removed":
            await self._async_handle_removal(node_id)
        else:
            await self._async_handle_discovery(node_id, ip, port, device_name)

    async def _async_handle_discovery(
        self,
        node_id: str,
        ip: str,
        port: int = DEFAULT_PORT,
        device_name: str | None = None,
    ) -> None:
        """Handle new device discovery.

        Throttles rapid discoveries and maintains list of discovered devices.

        Args:
            node_id: Device node ID.
            ip: Device IP address.
            port: Device port. Defaults to DEFAULT_PORT.
            device_name: Custom device name from mDNS TXT. Defaults to None.
        """
        async with self._lock:
            current_time = time.time()

            # Per-device throttling to prevent rapid updates for the same device
            # while allowing updates for different devices
            last_time = self._last_discovery_times.get(node_id, 0)
            if (
                node_id in self.seen_node_ids
                and current_time - last_time < DISCOVERY_THROTTLE_INTERVAL
            ):
                return

            self._last_discovery_times[node_id] = current_time

            if node_id not in self.seen_node_ids:
                self.seen_node_ids.add(node_id)
                device_info: dict[str, Any] = {
                    KEY_NODE_ID: node_id,
                    KEY_IP: ip,
                    KEY_PORT: port,
                    KEY_DEVICE_NAME: device_name,
                }
                self.devices.append(device_info)
                _LOGGER.info(
                    "Discovered new ESP device: %s at %s:%s (name=%s)",
                    node_id,
                    ip,
                    port,
                    device_name or "unnamed",
                )
            else:
                # Update existing device entry with new IP/port/name
                for device in self.devices:
                    if device.get(KEY_NODE_ID) == node_id:
                        device[KEY_IP] = ip
                        device[KEY_PORT] = port
                        device[KEY_DEVICE_NAME] = device_name
                        _LOGGER.debug(
                            "Updated device %s info: %s:%s (name=%s)",
                            node_id,
                            ip,
                            port,
                            device_name or "unnamed",
                        )
                        break

            if not self.discovered_event.is_set():
                self.discovered_event.set()

    async def _async_handle_removal(self, node_id: str) -> None:
        """Handle device removal.

        Removes device from seen list and devices list.

        Args:
            node_id: Device node ID to remove.
        """
        async with self._lock:
            if node_id in self.seen_node_ids:
                self.seen_node_ids.discard(node_id)
                self.devices = [d for d in self.devices if d[KEY_NODE_ID] != node_id]
                _LOGGER.info("Device %s removed from discovery list", node_id)
            else:
                _LOGGER.debug(
                    "Device %s was not in discovery list, removal skipped", node_id
                )
