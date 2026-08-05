# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP Device API - Main interface for ESP device management."""

# Allow relative imports within iot package
import asyncio
from collections.abc import Callable
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import ServiceBrowser

from ..cloud import RainMakerCLIClient
from ..discovery.network import MDNS_SERVICE_TYPE, GlobalMDNSListener
from ..managers.availability_manager import AvailabilityManager
from ..managers.connection_manager import ConnectionManager
from ..managers.device_registry import DeviceRegistry
from ..managers.entity_discovery import DeviceDiscoveryManager
from ..managers.property_manager import ConnectionCallbacks, PropertyManager
from ..parsers.property_parser import get_device_states
from ..payload.command_builder import build_device_command
from ..specs.keys import CONNECTION_MODE_CLOUD, CONNECTION_MODE_LOCAL

if TYPE_CHECKING:
    from zeroconf import Zeroconf

_LOGGER = logging.getLogger(__name__)


class ESPWeaverApi:  # pylint: disable=too-many-public-methods
    """API coordinator for ESP-Weaver devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        default_port: int = 8080,
        event_dispatcher: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize the ESP-Weaver API coordinator.

        Args:
            hass: Home Assistant instance.
            domain: Integration domain name.
            default_port: Default port for ESP devices.
            event_dispatcher: Callback for dispatching property update events.
                            Signature: (node_id: str, params_data: dict) -> None
                            If None, property updates won't fire events.
        """
        self.hass = hass
        self.domain = domain
        self.default_port = default_port
        self.registry = DeviceRegistry(default_port)

        self._availability = AvailabilityManager(hass, self.registry, default_port)
        self._discovery = DeviceDiscoveryManager(hass, domain, self.registry)
        self._property = PropertyManager(
            hass,
            domain,
            self.registry,
            event_dispatcher=event_dispatcher,
        )
        self._connection = ConnectionManager(
            hass,
            domain,
            self.registry,
            message_handler_factory=self._property.create_message_handler,
            property_extractor=get_device_states,
            property_processor=self.process_property_update,
        )

        # Inject connection callbacks after all managers are initialized
        # This avoids circular dependency during initialization
        self._property.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=self._connect_and_sync_device,
                reconnect_and_retry=self._reconnect_and_retry_property,
            )
        )

        self._global_browser: ServiceBrowser | None = None
        self._global_listener: GlobalMDNSListener | None = None

        # Cloud control: per-device client (supports mixed regions)
        self._cloud_clients: dict[str, RainMakerCLIClient] = {}
        # Mapping of node_id to connection_mode ("local" or "cloud")
        self._device_connection_modes: dict[str, str] = {}

    # Public Properties - Direct access to managers

    @property
    def devices(self) -> dict[str, Any]:
        """Get all registered devices as dict."""
        return {k: v.to_dict() for k, v in self.registry.get_all_devices().items()}

    # Cloud Control Methods

    def set_cloud_client(self, node_id: str, client: RainMakerCLIClient) -> None:
        """Set the cloud client for a specific device."""
        self._cloud_clients[node_id] = client

    def is_cloud_device(self, node_id: str) -> bool:
        """Check if a device uses cloud control."""
        return (
            self._device_connection_modes.get(node_id, CONNECTION_MODE_LOCAL)
            == CONNECTION_MODE_CLOUD
        )

    def _get_cloud_client(self, node_id: str) -> RainMakerCLIClient | None:
        """Get the cloud client for a device."""
        return self._cloud_clients.get(node_id)

    async def _set_cloud_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> bool:
        """Set device property via cloud using the same command builder as local."""
        client = self._get_cloud_client(node_id)
        if not client:
            _LOGGER.error("Cloud client not configured for %s", node_id)
            return False

        params = build_device_command(prop_name, value)
        if not params:
            _LOGGER.error("Failed to build cloud command for parameter: %s", prop_name)
            return False

        return await client.set_params(node_id, params)

    async def get_cloud_params(self, node_id: str) -> dict[str, Any] | None:
        """Get device parameters from cloud."""
        client = self._get_cloud_client(node_id)
        if not client:
            _LOGGER.debug("Cloud client not configured for %s", node_id)
            return None
        return await client.get_params(node_id)

    async def get_cloud_node_config(self, node_id: str) -> dict[str, Any] | None:
        """Get device node config from cloud."""
        client = self._get_cloud_client(node_id)
        if not client:
            _LOGGER.debug("Cloud client not configured for %s", node_id)
            return None
        return await client.get_node_details(node_id)

    async def is_cloud_device_online(self, node_id: str) -> bool | None:
        """Check if a cloud device is online (True/False/None if not found)."""
        client = self._get_cloud_client(node_id)
        if not client:
            return None
        return await client.get_node_connectivity(node_id)

    # Device Availability

    async def is_device_available_async(self, node_id: str) -> bool:
        """Check if device is available (async version)."""
        if self.is_cloud_device(node_id):
            online = await self.is_cloud_device_online(node_id)
            return bool(online)
        return await self.registry.is_device_available_async(node_id)

    async def is_mdns_available(
        self,
        node_id: str,
        expected_ip: str | None = None,
    ) -> bool:
        """Check if device's mDNS service is broadcasting."""
        return await self._availability.check_device_mdns_available(
            node_id, expected_ip
        )

    # Entity Discovery

    def is_discovery_completed(self, node_id: str) -> bool:
        """Check if entity discovery is completed for a device."""
        return self.registry.is_discovery_completed(node_id)

    def mark_discovery_completed(self, node_id: str) -> None:
        """Mark entity discovery as completed for a device."""
        self.registry.mark_discovery_completed(node_id)

    async def parse_and_discover_entities(
        self,
        node_id: str,
        properties: list,
        preferred_device_name: str | None = None,
    ) -> None:
        """Parse device configuration and trigger entity discovery."""
        await self._discovery.parse_and_discover_entities(
            node_id, properties, preferred_device_name
        )

    async def discover_from_cloud(
        self,
        node_id: str,
        node_config: dict[str, Any],
        cloud_params: dict[str, Any],
        device_name: str,
    ) -> None:
        """Discover entities from cloud device config."""
        await self._discovery.discover_from_cloud(
            node_id, node_config, cloud_params, device_name
        )

    # Property Operations

    async def _set_local_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> bool:
        """Set device property value via local control."""
        return await self._property.set_property(node_id, prop_name, value)

    async def set_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> bool:
        """Set device property, auto-selecting local or cloud based on device mode."""
        if self.is_cloud_device(node_id):
            return await self._set_cloud_property(node_id, prop_name, value)
        return await self._set_local_property(node_id, prop_name, value)

    def process_property_update(self, node_id: str, params_data: dict) -> None:
        """Process property update and fire events."""
        self._property.process_property_update(node_id, params_data)

    # Device Registration & Lifecycle

    def register_config_entry(
        self, node_id: str, entry_id: str, connection_mode: str = CONNECTION_MODE_LOCAL
    ) -> None:
        """Register a config entry for a device."""
        self.registry.register_config_entry(node_id, entry_id)
        self._device_connection_modes[node_id] = connection_mode

    def register_cloud_device(self, node_id: str) -> None:
        """Register a cloud-controlled device in the registry."""
        device = self.registry.register_device(node_id, "", 0)
        device.registered = True
        _LOGGER.info("Cloud device %s registered", node_id)

    async def register_device(
        self,
        node_id: str,
        ip: str,
        port: int | None = None,
        *,
        process_properties: bool = True,
    ) -> bool:
        """Register a device with the API coordinator.

        Note: Lock is handled internally by connect_and_sync() to avoid
        deadlock with nested lock acquisition.

        Args:
            node_id: Device node ID
            ip: Device IP address
            port: Device port (defaults to self.default_port)
            process_properties: If True (default), process properties and
                              update all entities. Use True for reconnection
                              scenarios where entities need state sync.
        """
        if port is None:
            port = self.default_port

        device = self.registry.register_device(node_id, ip, port)

        # Use device lock to avoid race condition between connection check
        # and state updates (device.registered, device.last_success)
        async with self.registry.device_lock(node_id):
            # Check if already connected (under lock to avoid race)
            client = self.registry.get_client(node_id)
            if client and await client.is_connected():
                device.registered = True
                device.last_success = asyncio.get_running_loop().time()
                return True

        # connect_and_sync() handles its own locking
        try:
            success, _ = await self._connect_and_sync_device(
                node_id,
                ip,
                port,
                process_properties=process_properties,
            )
        except (OSError, TimeoutError, ConnectionError, RuntimeError) as err:
            async with self.registry.device_lock(node_id):
                device.registered = False
            _LOGGER.debug("Failed to register device %s: %s", node_id, err)
            raise

        # Re-acquire lock for final state updates
        async with self.registry.device_lock(node_id):
            if success:
                device.registered = True
                device.last_success = asyncio.get_running_loop().time()
                return True

            device.registered = False
            return False

    async def unregister_device(self, node_id: str) -> None:
        """Unregister and clean up a device."""
        self._device_connection_modes.pop(node_id, None)
        self._cloud_clients.pop(node_id, None)
        await self._connection.disconnect_device(node_id)
        await self.registry.remove_device(node_id)

    async def cleanup(self) -> None:
        """Clean up API resources."""
        if self._global_browser:
            with contextlib.suppress(Exception):
                self._global_browser.cancel()
            self._global_browser = None

        self._global_listener = None
        self._cloud_clients.clear()
        self._device_connection_modes.clear()

        await self._connection.disconnect_all()

    # Service Operations

    async def start_services(self, enable_discovery: bool = True) -> None:
        """Start API services including mDNS browser.

        This method is idempotent - calling it multiple times is safe.
        """
        if not enable_discovery:
            return

        # Already started, skip
        if self._global_browser is not None:
            return

        try:
            self._global_listener = GlobalMDNSListener(self.hass, api=self)
            zc: Zeroconf = await zeroconf.async_get_instance(self.hass)
            self._global_browser = ServiceBrowser(
                zc, MDNS_SERVICE_TYPE, self._global_listener
            )
            _LOGGER.info("ESP-Weaver API services started with mDNS browser")
        except (OSError, RuntimeError, ValueError) as err:
            # OSError: network-level errors, RuntimeError: zeroconf issues
            # ValueError: invalid configuration
            _LOGGER.warning("Failed to start mDNS browser: %s", err)

    def get_device_data(self, node_id: str) -> dict | None:
        """Get device data for diagnostics."""
        device = self.registry.get_device(node_id)
        return device.to_dict() if device else None

    async def update_device(
        self,
        node_id: str,
        ip: str,
        port: int | None = None,
    ) -> None:
        """Update device information and attempt connection.

        Args:
            node_id: Device node ID
            ip: Device IP address
            port: Device port (defaults to self.default_port)
        """
        await self.register_device(node_id, ip, port)

    # Internal Connection Methods

    async def _connect_and_sync_device(
        self,
        node_id: str,
        ip: str,
        port: int,
        *,
        process_properties: bool = True,
    ) -> tuple[bool, list | None]:
        """Connect to device and sync properties.

        Args:
            node_id: Device node ID
            ip: Device IP address
            port: Device port
            process_properties: If True, process properties and trigger entity
                              discovery. If False, only establish connection
                              without processing (used during initial setup
                              when Coordinator handles discovery).
        """
        success, properties = await self._connection.connect_and_sync(
            node_id,
            ip,
            port,
            process_properties=process_properties,
        )

        # Only trigger discovery when process_properties=True
        # Initial setup uses process_properties=False because Coordinator
        # handles discovery. Reconnection uses process_properties=True to
        # sync entity states
        if (
            process_properties
            and success
            and properties
            and not self.registry.is_discovery_completed(node_id)
        ):
            await self._discovery.parse_and_discover_entities(node_id, properties)
            self.registry.mark_discovery_completed(node_id)

        return (success, properties)

    async def _reconnect_and_retry_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> bool:
        """Reconnect and retry property set operation.

        Syncs all entity states before retrying the property set.
        """
        device = self.registry.get_device(node_id)
        if not device or not device.ip:
            _LOGGER.warning(
                "Cannot reconnect device %s: device info not found", node_id
            )
            return False

        port = device.port or self.default_port

        _LOGGER.debug(
            "Attempting reconnection and retry for device %s (prop=%s)",
            node_id,
            prop_name,
        )

        # Sync all entities with device state before executing user's command
        success, _ = await self._connection.connect_and_sync(
            node_id,
            device.ip,
            port,
            process_properties=True,
        )
        if not success:
            _LOGGER.warning(
                "Reconnection failed for device %s, cannot retry property set",
                node_id,
            )
            return False

        _LOGGER.debug("Reconnection successful, retrying property set for %s", node_id)

        result = await self._set_local_property(node_id, prop_name, value)

        if result:
            # Update last_success under device lock to avoid race with register_device
            async with self.registry.device_lock(node_id):
                device.last_success = asyncio.get_running_loop().time()
            _LOGGER.info(
                "Property '%s' retry successful for device %s", prop_name, node_id
            )
        else:
            _LOGGER.error(
                "Property '%s' retry failed for device %s after reconnection",
                prop_name,
                node_id,
            )

        return result
