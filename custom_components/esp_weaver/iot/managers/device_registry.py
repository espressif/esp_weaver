# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device registry for ESP IoT integration."""

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from ..client.client import ESPLocalCtrlClient
from ..specs.device_specs import DEFAULT_PORT
from ..specs.keys import (
    KEY_CURRENT_VALUES,
    KEY_DEVICE_INFO,
    KEY_IP,
    KEY_LAST_SUCCESS,
    KEY_NODE_ID,
    KEY_PARSED_CONFIG,
    KEY_PORT,
    KEY_PROPERTIES,
    KEY_REGISTERED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Device information container."""

    node_id: str
    ip: str
    port: int = DEFAULT_PORT
    registered: bool = False
    device_info: dict[str, Any] = field(default_factory=dict)
    parsed_config: dict[str, Any] = field(default_factory=dict)
    current_values: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    last_success: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            KEY_NODE_ID: self.node_id,
            KEY_IP: self.ip,
            KEY_PORT: self.port,
            KEY_REGISTERED: self.registered,
            KEY_DEVICE_INFO: self.device_info,
            KEY_PARSED_CONFIG: self.parsed_config,
            KEY_CURRENT_VALUES: self.current_values,
            KEY_PROPERTIES: self.properties,
            KEY_LAST_SUCCESS: self.last_success,
        }


class DeviceRegistry:
    """Centralized registry for device state management."""

    def __init__(self, default_port: int = DEFAULT_PORT) -> None:
        """Initialize the device registry."""
        self.default_port = default_port
        self._devices: dict[str, DeviceInfo] = {}
        self._clients: dict[str, ESPLocalCtrlClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._discovery_completed: set[str] = set()
        self._discovered_platforms: set[str] = set()
        self._config_entries: dict[str, str] = {}

    # Device Operations

    def register_device(
        self,
        node_id: str,
        ip: str,
        port: int | None = None,
    ) -> DeviceInfo:
        """Register a new device or update existing one."""
        if port is None:
            port = self.default_port

        if node_id in self._devices:
            device = self._devices[node_id]
            device.ip = ip
            device.port = port
            device.registered = True
        else:
            device = DeviceInfo(node_id=node_id, ip=ip, port=port, registered=True)
            self._devices[node_id] = device

        return device

    def get_device(self, node_id: str) -> DeviceInfo | None:
        """Get device information."""
        return self._devices.get(node_id)

    def get_all_devices(self) -> dict[str, DeviceInfo]:
        """Get all registered devices."""
        return self._devices.copy()

    async def remove_device(self, node_id: str) -> None:
        """Remove a device from the registry."""
        client = self._clients.pop(node_id, None)
        if client:
            try:
                await client.disconnect()
            except (OSError, TimeoutError):
                _LOGGER.debug(
                    "Failed to disconnect client for %s during removal", node_id
                )
        self._devices.pop(node_id, None)
        self._locks.pop(node_id, None)
        self._discovery_completed.discard(node_id)
        self._config_entries.pop(node_id, None)

    # Client Operations

    def set_client(self, node_id: str, client: ESPLocalCtrlClient) -> None:
        """Set the ESP Local Control client for a device."""
        self._clients[node_id] = client

    def get_client(self, node_id: str) -> ESPLocalCtrlClient | None:
        """Get the ESP Local Control client for a device."""
        return self._clients.get(node_id)

    def remove_client(self, node_id: str) -> ESPLocalCtrlClient | None:
        """Remove and return the client for a device."""
        return self._clients.pop(node_id, None)

    def has_client(self, node_id: str) -> bool:
        """Check if device has an active client."""
        return node_id in self._clients

    def get_all_node_ids(self) -> list[str]:
        """Return all node IDs that have active clients."""
        return list(self._clients.keys())

    # Lock Operations

    def get_lock(self, node_id: str) -> asyncio.Lock:
        """Get or create a lock for a device."""
        return self._locks.setdefault(node_id, asyncio.Lock())

    def device_lock(self, node_id: str) -> asyncio.Lock:
        """Get lock for a device (can be used with 'async with')."""
        return self.get_lock(node_id)

    # Discovery Tracking

    def mark_discovery_completed(self, node_id: str) -> None:
        """Mark initial discovery as completed for a device."""
        self._discovery_completed.add(node_id)

    def is_discovery_completed(self, node_id: str) -> bool:
        """Check if initial discovery is completed for a device."""
        return node_id in self._discovery_completed

    # Platform Discovery Tracking

    def add_discovered_platform(self, platform: str) -> bool:
        """Add a platform to discovered set. Returns True if newly added."""
        if platform in self._discovered_platforms:
            return False
        self._discovered_platforms.add(platform)
        return True

    # Config Entry Mapping

    def register_config_entry(self, node_id: str, entry_id: str) -> None:
        """Register a config entry for a device."""
        self._config_entries[node_id] = entry_id

    def get_config_entry_id(self, node_id: str) -> str | None:
        """Get the config entry ID for a device."""
        return self._config_entries.get(node_id)

    # Availability Check

    def is_device_available(self, node_id: str) -> bool:
        """Check if device is available (registered and has active client)."""
        device = self.get_device(node_id)
        if not device or not device.registered:
            return False

        client = self.get_client(node_id)
        if not client:
            return False

        try:
            return client.session_established and client.transport is not None
        except AttributeError:
            # Client in invalid state
            return False

    async def is_device_available_async(self, node_id: str) -> bool:
        """Check if device is available with async connection verification."""
        if not self.is_device_available(node_id):
            return False

        # Re-fetch client (could have been removed between is_device_available check)
        client = self.get_client(node_id)
        if not client:
            return False

        try:
            return await client.is_connected()
        except (OSError, AttributeError):
            # OSError: network-level errors
            # AttributeError: client in invalid state
            return False
