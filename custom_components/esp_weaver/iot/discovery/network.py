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
from typing import TYPE_CHECKING, Any, Final, Protocol

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

from ..discovered_device import DiscoveredDevice
from ..host import IotHost
from ..specs.device_specs import DEFAULT_PORT

if TYPE_CHECKING:
    from ..managers.device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

DEVICE_SERVICE_TYPE: Final[str] = "_esp_local_ctrl"
PROTO: Final[str] = "_tcp"
MDNS_SERVICE_TYPE: Final[str] = f"{DEVICE_SERVICE_TYPE}.{PROTO}.local."

# Seconds spent collecting announcements, then letting the last of them land.
DISCOVERY_TIMEOUT: Final[float] = 20.0
DISCOVERY_SETTLE_TIME: Final[float] = 3.0
# How long one device is given to answer for its own record.
SERVICE_INFO_TIMEOUT_MS: Final[int] = 3000

# A device re-announcing itself in a burst is one device, not several.
DISCOVERY_THROTTLE_INTERVAL: Final[int] = 5


class DeviceHost(Protocol):
    """All that acting on an mDNS announcement needs of the API.

    A protocol rather than the API itself, which builds the listener.
    """

    @property
    def registry(self) -> "DeviceRegistry":
        """Where devices and their addresses are recorded."""

    def is_cloud_device(self, node_id: str) -> bool:
        """Say whether the node is controlled through the cloud instead."""

    async def register_device(
        self, node_id: str, ip: str, port: int | None = None
    ) -> bool:
        """Record the node at this address and connect to it there."""


def ipv4_of(service_info: ServiceInfo | None) -> str | None:
    """Return the first IPv4 address a service advertises. IPv4 only.."""
    if service_info is None:
        return None
    ipv4 = next((addr for addr in service_info.addresses if len(addr) == 4), None)
    return socket.inet_ntoa(ipv4) if ipv4 else None


def _is_valid_node_id(node_id_str: str) -> bool:
    """Check if string is a valid node ID format."""
    id_pattern = r"^[0-9A-Za-z_-]{1,64}$"
    return bool(re.match(id_pattern, node_id_str))


def _extract_node_id_from_service(
    service_info: ServiceInfo | None,
) -> tuple[str | None, str | None]:
    """Read the node id and name out of a service's TXT record, if it has them.

    The keys are bytes because that is how zeroconf hands TXT records over.
    """
    if not service_info or not service_info.properties:
        return (None, None)

    device_name = None
    if b"device_name" in service_info.properties:
        device_name_bytes = service_info.properties[b"device_name"]
        if device_name_bytes is not None:
            device_name = device_name_bytes.decode("utf-8", errors="replace")

    if b"node_id" in service_info.properties:
        node_id_bytes = service_info.properties[b"node_id"]
        if node_id_bytes is None:
            return (None, device_name)
        id_value = node_id_bytes.decode("utf-8", errors="replace")
        if _is_valid_node_id(id_value):
            return (id_value, device_name)

    return (None, device_name)


def _txt_value(service_info: ServiceInfo, key: bytes) -> str | None:
    """Return one TXT record's value as text, or None if absent or empty."""
    value = service_info.properties.get(key) if service_info.properties else None
    if not value:
        return None
    return value.decode("utf-8", errors="replace")


def extract_model_from_service(service_info: ServiceInfo | None) -> str | None:
    """Return the ``model`` TXT record as sent, or None where absent.

    The record also carries ``type``, the node type; it is not read, since the
    product is matched on the model alone.
    """
    if not service_info or not service_info.properties:
        return None
    return _txt_value(service_info, b"model")


async def async_discover_devices(host: IotHost) -> list[DiscoveredDevice]:
    """Browse for ESP devices for a fixed window, and return what answered."""
    try:
        zc = await host.zeroconf()
        listener = ESPDeviceListener(host)
        browser = ServiceBrowser(zc, MDNS_SERVICE_TYPE, listener)

        try:
            await asyncio.wait_for(
                listener.discovered_event.wait(), timeout=DISCOVERY_TIMEOUT
            )
            await asyncio.sleep(DISCOVERY_SETTLE_TIME)
        except TimeoutError:
            _LOGGER.debug(
                "Device discovery timed out after %s seconds, returning %d device(s)",
                DISCOVERY_TIMEOUT,
                len(listener.devices),
            )
        finally:
            with contextlib.suppress(OSError, RuntimeError):
                browser.cancel()
            # Cancelling the browser does not stop lookups already running.
            listener.cancel_pending()
        return list(listener.devices)

    except OSError as err:
        _LOGGER.error("Network error during device discovery: %s", err)
        return []


class BaseMDNSListener(ServiceListener):
    """From a zeroconf callback to an identified device; subclasses act on it."""

    def __init__(self, host: IotHost) -> None:
        """Initialize the base listener."""
        self.host = host
        self._tasks: set[asyncio.Task[Any]] = set()

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

        def _start() -> None:
            task = self.host.create_task(
                self._async_handle_service_change(type_, name, change_type),
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        self.host.loop.call_soon_threadsafe(_start)

    def cancel_pending(self) -> None:
        """Drop callbacks that have not finished, used when the API unloads."""
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    async def _async_handle_service_change(
        self, type_: str, name: str, change_type: str
    ) -> None:
        """Common handler for all service changes (async)."""
        try:
            info = AsyncServiceInfo(type_, name)
            zc = await self.host.zeroconf()
            if not await info.async_request(zc, SERVICE_INFO_TIMEOUT_MS):
                return

            if not info.addresses:
                return

            node_id, device_name = _extract_node_id_from_service(info)
            if not node_id:
                return

            ip = ipv4_of(info)
            if ip is None:
                _LOGGER.debug("No IP address found for service %s, skipping", name)
                return
            await self._async_process_service(
                DiscoveredDevice(
                    ip=ip,
                    node_id=node_id,
                    port=info.port or DEFAULT_PORT,
                    device_name=device_name,
                    model=extract_model_from_service(info),
                ),
                change_type,
            )

        except (AttributeError, KeyError, ValueError, OSError) as err:
            _LOGGER.debug(
                "Error handling service %s for %s: %s", change_type, name, err
            )

    async def _async_process_service(
        self, device: DiscoveredDevice, change_type: str
    ) -> None:
        """Act on one identified device. Subclasses say what that means.

        ``device`` is what the announcement said, security fields unset;
        ``change_type`` is one of ``added``, ``updated`` or ``removed``.
        """


class GlobalMDNSListener(BaseMDNSListener):
    """Follows devices that are already set up, keeping them reachable when they move.

    Announcements from anything else are dropped.
    """

    def __init__(self, host: IotHost, api: "DeviceHost") -> None:
        """Initialize the global listener."""
        super().__init__(host)
        self.api = api

    async def _async_process_service(
        self, device: DiscoveredDevice, change_type: str
    ) -> None:
        """Process service change for configured devices only."""
        node_id, ip, port = device.node_id, device.ip, device.port
        if not self._is_locally_controlled(node_id):
            return

        _LOGGER.debug(
            "mDNS %s event for configured device %s at %s:%s",
            change_type,
            node_id,
            ip,
            port,
        )

        # Whatever happens to the connection below, the host's record of
        # where the node lives is brought up to date.
        self.host.address_seen(node_id, ip, port)

        # Do not reconnect a connected device on an announcement; only update
        # its address.
        if self.api.registry.get_client(node_id) and self._record_endpoint(
            node_id, ip, port
        ):
            return

        try:
            await self.api.register_device(node_id, ip, port)
            _LOGGER.info(
                "Successfully updated device %s via mDNS at %s:%s",
                node_id,
                ip,
                port,
            )
        except OSError as err:
            # Network errors during device update are expected and recoverable
            _LOGGER.warning(
                "Failed to update device %s at %s:%s: %s",
                node_id,
                ip,
                port,
                err,
            )

    def _is_locally_controlled(self, node_id: str) -> bool:
        """Say whether this node is one that mDNS should act on at all."""
        if node_id not in self.host.configured_node_ids():
            _LOGGER.debug(
                "Device %s not in configured entries, skipping update", node_id
            )
            return False

        # A cloud device is reached through the cloud API; a local connect
        # with the placeholder security_version=0 would only fail.
        if self.api.is_cloud_device(node_id):
            _LOGGER.debug(
                "Device %s is cloud-controlled, skipping local connection", node_id
            )
            return False

        return True

    def _record_endpoint(self, node_id: str, ip: str, port: int) -> bool:
        """Note where a device is now answering from, without connecting to it.

        Returns:
            True if the registry knew the device; False if a full reconnection
            is needed.
        """
        device = self.api.registry.get_device(node_id)
        if device is None:
            return False

        if (device.ip, device.port) == (ip, port):
            _LOGGER.debug(
                "Device %s already connected at %s:%s, skipping update",
                node_id,
                ip,
                port,
            )
            return True

        _LOGGER.info(
            "Updated IP/port for connected device %s: %s:%s -> %s:%s",
            node_id,
            device.ip,
            device.port,
            ip,
            port,
        )
        self.api.registry.register_device(node_id, ip, port)
        return True


class ESPDeviceListener(BaseMDNSListener):
    """Collects every ESP device on the network, for the config flow to offer."""

    def __init__(self, host: IotHost) -> None:
        """Initialize the device listener."""
        super().__init__(host)
        self.devices: list[DiscoveredDevice] = []
        self.discovered_event: asyncio.Event = asyncio.Event()
        # Throttled per device, so that one device announcing itself in a burst
        # cannot hold up the announcements of any other.
        self._last_discovery_times: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    def _find(self, node_id: str) -> DiscoveredDevice | None:
        """Return the device already listed under this node id, if any."""
        return next((d for d in self.devices if d.node_id == node_id), None)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle removal of a service."""
        self._schedule_service_handling(type_, name, "removed")

    async def _async_process_service(
        self, device: DiscoveredDevice, change_type: str
    ) -> None:
        """Process discovered or removed service."""
        if change_type == "removed":
            await self._async_handle_removal(device.node_id)
        else:
            await self._async_handle_discovery(device)

    async def _async_handle_discovery(self, device: DiscoveredDevice) -> None:
        """Add a device to the list, or bring the entry it already has up to date."""
        async with self._lock:
            current_time = time.time()
            known = self._find(device.node_id)

            last_time = self._last_discovery_times.get(device.node_id, 0)
            if known and current_time - last_time < DISCOVERY_THROTTLE_INTERVAL:
                return

            self._last_discovery_times[device.node_id] = current_time

            if known is None:
                self.devices.append(device)
                _LOGGER.info(
                    "Discovered new ESP device: %s at %s:%s (name=%s, model=%s)",
                    device.node_id,
                    device.ip,
                    device.port,
                    device.device_name or "unnamed",
                    device.model or "-",
                )
            else:
                # A later announcement is the device's current word.
                known.ip = device.ip
                known.port = device.port
                known.device_name = device.device_name
                known.model = device.model
                _LOGGER.debug(
                    "Updated device %s info: %s:%s (name=%s)",
                    device.node_id,
                    device.ip,
                    device.port,
                    device.device_name or "unnamed",
                )

            if not self.discovered_event.is_set():
                self.discovered_event.set()

    async def _async_handle_removal(self, node_id: str) -> None:
        """Take a device that has gone away back off the list."""
        async with self._lock:
            if self._find(node_id) is None:
                _LOGGER.debug(
                    "Device %s was not in discovery list, removal skipped", node_id
                )
                return
            self.devices = [d for d in self.devices if d.node_id != node_id]
            _LOGGER.info("Device %s removed from discovery list", node_id)
