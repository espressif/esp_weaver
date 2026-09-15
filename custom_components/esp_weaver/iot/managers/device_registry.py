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
    KEY_FIRMWARE_VERSION,
    KEY_IP,
    KEY_LAST_SUCCESS,
    KEY_NODE_ID,
    KEY_PORT,
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
    # The version the node's config named, kept apart from the config because
    # an OTA changes it without changing the object model around it.
    firmware_version: str | None = None
    last_success: float | None = None
    # The node's config and params exactly as reported; read by diagnostics.
    raw_config: dict[str, Any] = field(default_factory=dict)
    raw_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            KEY_NODE_ID: self.node_id,
            KEY_IP: self.ip,
            KEY_PORT: self.port,
            KEY_REGISTERED: self.registered,
            KEY_FIRMWARE_VERSION: self.firmware_version,
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

    def register_device(
        self,
        node_id: str,
        ip: str,
        port: int | None = None,
    ) -> DeviceInfo:
        """Record a device at this address, or move the record it has there.

        ``registered`` is left to whoever opens the session.
        """
        if port is None:
            port = self.default_port

        if (device := self._devices.get(node_id)) is not None:
            device.ip = ip
            device.port = port
        else:
            device = DeviceInfo(node_id=node_id, ip=ip, port=port)
            self._devices[node_id] = device

        return device

    def get_device(self, node_id: str) -> DeviceInfo | None:
        """Get device information."""
        return self._devices.get(node_id)

    def get_all_devices(self) -> dict[str, DeviceInfo]:
        """Get all registered devices."""
        return self._devices.copy()

    def remove_device(self, node_id: str) -> None:
        """Forget a device, its client included. Bookkeeping only, no I/O."""
        self._clients.pop(node_id, None)
        self._devices.pop(node_id, None)
        # A lock somebody holds stays, or the registration under it would no
        # longer exclude the next one; a free lock has nothing left to guard.
        if (lock := self._locks.get(node_id)) is not None and not lock.locked():
            del self._locks[node_id]
        self._discovery_completed.discard(node_id)

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

    def device_lock(self, node_id: str) -> asyncio.Lock:
        """Return the one lock serialising work on a device, creating it if new."""
        return self._locks.setdefault(node_id, asyncio.Lock())

    def mark_discovery_completed(self, node_id: str) -> None:
        """Mark initial discovery as completed for a device."""
        self._discovery_completed.add(node_id)

    def is_discovery_completed(self, node_id: str) -> bool:
        """Check if initial discovery is completed for a device."""
        return node_id in self._discovery_completed

    def has_session(self, node_id: str) -> bool:
        """Return True if a registered device holds a client with a session.

        What the registry knows, without touching the network: a session
        whose socket has since died still counts here. Whether the device is
        reachable is ``is_device_available_async``'s question.
        """
        device = self.get_device(node_id)
        if not device or not device.registered:
            return False

        client = self.get_client(node_id)
        return client is not None and client.has_session

    async def is_device_available_async(self, node_id: str) -> bool:
        """Return True if the device's session is up and its socket healthy."""
        if not self.has_session(node_id):
            return False

        # Re-fetch: the client could have been removed meanwhile.
        client = self.get_client(node_id)
        if not client:
            return False
        # The client answers False to its own socket errors.
        return await client.is_connected()
