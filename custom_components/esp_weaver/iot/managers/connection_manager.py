# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Connection management for ESP IoT devices."""

from collections.abc import Callable
import contextlib
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from ..client.client import ESPLocalCtrlClient
from ..specs.events import EVENT_CONNECTION_ERROR
from ..specs.keys import (
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_SECURITY_VERSION,
    KEY_NODE_ID,
)

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)


class ConnectionManager:
    """Manages ESP Local Control connections and sessions."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        registry: "DeviceRegistry",
        *,
        message_handler_factory: Callable[[str], Callable] | None = None,
        property_extractor: Callable[[list], dict] | None = None,
        property_processor: Callable[[str, dict], None] | None = None,
    ) -> None:
        """Initialize the connection manager.

        Args:
            hass: Home Assistant instance
            domain: Integration domain name
            registry: Device registry for state management
            message_handler_factory: Factory to create message handlers for devices
            property_extractor: Callback to extract values from properties
            property_processor: Callback to process property updates
        """
        self.hass = hass
        self.domain = domain
        self.registry = registry
        self._message_handler_factory = message_handler_factory
        self._property_extractor = property_extractor
        self._property_processor = property_processor

    # Connection Error Handling

    def _on_connection_error(self, node_id: str) -> None:
        """Handle connection error from ESPLocalCtrlClient."""
        self.hass.bus.fire(
            EVENT_CONNECTION_ERROR,
            {KEY_NODE_ID: node_id},
        )

    # Connection Establishment

    async def _establish_session(
        self,
        node_id: str,
        ip: str,
        port: int,
    ) -> bool:
        """Establish ESP Local Control session with the device."""
        # Check if already connected
        existing_client = self.registry.get_client(node_id)
        if existing_client:
            if await existing_client.is_connected():
                return True
            try:
                await existing_client.disconnect()
            except (OSError, RuntimeError) as err:
                _LOGGER.debug(
                    "Error disconnecting existing client %s: %s", node_id, err
                )
            finally:
                self.registry.remove_client(node_id)

        client = None
        try:
            pop_value, security_version = self._get_device_config(node_id)

            client = ESPLocalCtrlClient(
                node_id,
                ip,
                port=port,
                pop=pop_value,
                security_mode=security_version,
            )

            if self._message_handler_factory:
                client.add_message_callback(self._message_handler_factory(node_id))

            # Register connection error callback to trigger Coordinator refresh
            client.set_connection_error_callback(self._on_connection_error)

            connected = await client.connect()

            if connected:
                self.registry.set_client(node_id, client)
                _LOGGER.info(
                    "Successfully connected to device %s at %s:%s", node_id, ip, port
                )
            else:
                _LOGGER.warning(
                    "Failed to connect to device %s at %s:%s", node_id, ip, port
                )
                try:
                    await client.disconnect()
                except (OSError, RuntimeError) as err:
                    _LOGGER.debug(
                        "Error during disconnect after failed connect for %s: %s",
                        node_id,
                        err,
                    )

        except ConnectionError as err:
            _LOGGER.error(
                "Connection error for device %s at %s:%s: %s",
                node_id,
                ip,
                port,
                err,
            )
            if client is not None:
                with contextlib.suppress(OSError, RuntimeError):
                    await client.disconnect()
            return False
        except (RuntimeError, ValueError):
            # RuntimeError: protocol/session errors
            # ValueError: invalid configuration
            _LOGGER.exception(
                "Unexpected error connecting to device %s at %s:%s",
                node_id,
                ip,
                port,
            )
            if client is not None:
                with contextlib.suppress(OSError, RuntimeError):
                    await client.disconnect()
            return False

        return connected

    async def connect_and_sync(
        self,
        node_id: str,
        ip: str,
        port: int,
        *,
        process_properties: bool = True,
    ) -> tuple[bool, list | None]:
        """Connect to device and sync properties.

        This is the primary method for establishing device connections.
        It handles connection establishment and optional property synchronization.

        Uses device_lock to prevent concurrent connection attempts from
        multiple sources (Coordinator reconnect, mDNS discovery, fast_reconnect).

        Args:
            node_id: Device node ID
            ip: Device IP address
            port: Device port
            process_properties: If True, fetch and process properties.
                              If False, only establish connection without
                              fetching properties (used during initial setup
                              when Coordinator handles property fetching).

        Returns:
            Tuple of (success: bool, properties: list | None)
        """
        async with self.registry.device_lock(node_id):
            # Check if already connected - no need to reconnect
            existing_client = self.registry.get_client(node_id)
            if existing_client and await existing_client.is_connected():
                _LOGGER.debug(
                    "Device %s already connected, reusing existing connection", node_id
                )
                # Skip property fetch if not processing (e.g., initial setup)
                if not process_properties:
                    return (True, None)
                properties = await self._fetch_device_properties(node_id)
                if properties:
                    self._process_fetched_properties(node_id, properties)
                return (True, properties)

            # Establish connection (will clean up disconnected client if exists)
            if not await self._establish_session(node_id, ip, port):
                return (False, None)

            # Skip property fetch if not processing (e.g., initial setup)
            # Coordinator will handle property fetching and entity discovery
            if not process_properties:
                _LOGGER.debug(
                    "Connection established for %s, skipping property fetch", node_id
                )
                return (True, None)

            # Fetch and process properties
            properties = await self._fetch_device_properties(node_id)

            if properties:
                _LOGGER.debug(
                    "Fetched %d properties from device %s",
                    len(properties),
                    node_id,
                )
                self._process_fetched_properties(node_id, properties)
            else:
                _LOGGER.warning(
                    "No properties fetched from device %s after connection", node_id
                )

            return (True, properties)

    async def _fetch_device_properties(self, node_id: str) -> list | None:
        """Fetch properties from device.

        Args:
            node_id: Device node ID

        Returns:
            List of properties or None
        """
        client = self.registry.get_client(node_id)
        if not client:
            return None

        try:
            return await client.get_property_values()
        except (OSError, TimeoutError, ConnectionError) as err:
            # OSError: network-level errors
            # TimeoutError: property fetch timeout
            # ConnectionError: connection lost
            _LOGGER.debug(
                "Failed to fetch properties from device %s (%s:%s): %s",
                node_id,
                client.ip,
                client.port,
                err,
            )
            return None

    def _process_fetched_properties(
        self,
        node_id: str,
        properties: list,
    ) -> None:
        """Process fetched properties and fire events.

        Args:
            node_id: Device node ID
            properties: Fetched properties
        """
        if not self._property_extractor or not self._property_processor:
            return

        current_values = self._property_extractor(properties)
        if current_values:
            self._property_processor(node_id, current_values)

    # Disconnection

    async def disconnect_device(self, node_id: str) -> None:
        """Disconnect ESP Local Control client for a device.

        Args:
            node_id: Device node ID
        """
        client = self.registry.remove_client(node_id)

        if client:
            try:
                await client.disconnect()
            except (OSError, RuntimeError) as err:
                # OSError: network errors during disconnect
                # RuntimeError: client in invalid state
                _LOGGER.warning("Error disconnecting device %s: %s", node_id, err)

    async def disconnect_all(self) -> None:
        """Disconnect all ESP Local Control clients."""
        # Take a snapshot to avoid modification during iteration
        node_ids = list(self.registry.get_all_node_ids())
        for node_id in node_ids:
            await self.disconnect_device(node_id)

    # Configuration Helpers

    def _get_device_config(self, node_id: str) -> tuple[str, int]:
        """Get device configuration from config entry.

        Performs a single iteration through config entries to retrieve
        both PoP and security version values.

        Args:
            node_id: Device node ID

        Returns:
            Tuple of (pop_value, security_version).
            Defaults to ("", 1) if not found in config.
        """
        for entry in self.hass.config_entries.async_entries(self.domain):
            entry_node_id = entry.data.get(CONF_NODE_ID, "")
            if entry_node_id == node_id:
                pop = entry.data.get(CONF_CUSTOM_POP, "")
                security_version = entry.data.get(CONF_SECURITY_VERSION, 1)
                return (pop, security_version)

        return ("", 1)
