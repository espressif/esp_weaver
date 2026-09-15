# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Connection management for ESP IoT devices."""

from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING

from ..client.client import ESPLocalCtrlClient
from ..host import IotHost

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)


async def _discard_client(client: ESPLocalCtrlClient | None, node_id: str) -> None:
    """Close a client that is not going to be used, whatever state it is in."""
    if client is None:
        return

    try:
        await client.disconnect()
    except (OSError, RuntimeError) as err:
        _LOGGER.debug("Error disconnecting client %s: %s", node_id, err)


class ConnectionManager:
    """Manages ESP Local Control connections and sessions."""

    def __init__(
        self,
        host: IotHost,
        registry: "DeviceRegistry",
        *,
        message_handler_factory: Callable[[str], Callable] | None = None,
        on_properties: Callable[[str, list], Awaitable[None]] | None = None,
        device_auth: Callable[[str], tuple[str, int]] | None = None,
    ) -> None:
        """Initialize the connection manager.

        ``on_properties(node_id, properties)`` is handed what a node reports
        when its state is read back after connecting. ``device_auth`` returns
        ``(pop, security_version)`` for a node, so this layer never reads
        config entries itself.
        """
        self.host = host
        self.registry = registry
        self._message_handler_factory = message_handler_factory
        self._on_properties = on_properties
        self._device_auth = device_auth

    def _on_connection_error(self, node_id: str) -> None:
        """Tell the host that a node's client has lost its connection."""
        self.host.connection_lost(node_id)

    async def _establish_session(
        self,
        node_id: str,
        ip: str,
        port: int,
    ) -> bool:
        """Establish ESP Local Control session with the device."""
        if await self._reuse_or_drop_client(node_id):
            return True

        # connect() reports failure rather than raising it, so there is no
        # network error to catch here.
        client = self._build_client(node_id, ip, port)
        if not await client.connect():
            _LOGGER.warning(
                "Failed to connect to device %s at %s:%s", node_id, ip, port
            )
            await _discard_client(client, node_id)
            return False

        self.registry.set_client(node_id, client)
        _LOGGER.info("Successfully connected to device %s at %s:%s", node_id, ip, port)
        return True

    async def _reuse_or_drop_client(self, node_id: str) -> bool:
        """Keep a working client for this node, or clear the way for a new one.

        Returns:
            True if the node already has a live connection.
        """
        existing_client = self.registry.get_client(node_id)
        if existing_client is None:
            return False

        if await existing_client.is_connected():
            _LOGGER.debug(
                "Device %s already connected, reusing existing connection", node_id
            )
            return True

        await _discard_client(existing_client, node_id)
        self.registry.remove_client(node_id)
        return False

    def _build_client(self, node_id: str, ip: str, port: int) -> ESPLocalCtrlClient:
        """Build a client for one node, wired up but not yet connected."""
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
        return client

    async def connect_and_sync(
        self,
        node_id: str,
        ip: str,
        port: int,
        *,
        process_properties: bool = True,
    ) -> tuple[bool, list | None]:
        """Connect to a device, and unless told otherwise read its state back.

        Held under the node's lock: a reconnect, an mDNS announcement and a
        retried write can all arrive at once.

        Returns:
            Whether the device is connected, and its properties if fetched.
        """
        async with self.registry.device_lock(node_id):
            # A live session is kept; a dead one is replaced.
            if not await self._establish_session(node_id, ip, port):
                return (False, None)

            if not process_properties:
                _LOGGER.debug(
                    "Connection established for %s, skipping property fetch", node_id
                )
                return (True, None)

            properties = await self._fetch_device_properties(node_id)

            if properties:
                _LOGGER.debug(
                    "Fetched %d properties from device %s",
                    len(properties),
                    node_id,
                )
                if self._on_properties is not None:
                    await self._on_properties(node_id, properties)
            else:
                _LOGGER.warning(
                    "No properties fetched from device %s after connection", node_id
                )

            return (True, properties)

    async def _fetch_device_properties(self, node_id: str) -> list | None:
        """Ask the device for its properties, or None if it could not be asked."""
        client = self.registry.get_client(node_id)
        if not client:
            return None
        # The client catches its own network errors and answers None for them.
        return await client.get_property_values()

    async def disconnect_device(self, node_id: str) -> None:
        """Drop a device's client, whether or not it goes quietly."""
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

    def _get_device_config(self, node_id: str) -> tuple[str, int]:
        """Return the PoP and security version the integration stored for this node."""
        if self._device_auth is None:
            return ("", 1)
        return self._device_auth(node_id)
