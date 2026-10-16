# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP Device API - Main interface for ESP device management."""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from zeroconf import ServiceBrowser

from ..cloud import RainMakerCLIClient
from ..discovery import MDNS_SERVICE_TYPE, GlobalMDNSListener
from ..host import IotHost
from ..local_ctrl import local_ctrl
from ..managers.availability_manager import AvailabilityManager
from ..managers.connection_manager import ConnectionManager
from ..managers.device_registry import DeviceInfo, DeviceRegistry
from ..managers.entity_discovery import BindingResolver, DeviceDiscoveryManager
from ..managers.property_manager import (
    ConnectionCallbacks,
    PropertyManager,
    WriteOutcome,
)
from ..parsers.property_parser import get_raw_params
from ..specs.device_specs import DEFAULT_PORT
from ..specs.keys import CONNECTION_MODE_CLOUD, CONNECTION_MODE_LOCAL, KEY_PROPERTIES

if TYPE_CHECKING:
    from zeroconf import Zeroconf

_LOGGER = logging.getLogger(__name__)


class ESPWeaverApi:  # pylint: disable=too-many-public-methods
    """Facade over the IoT managers for one Home Assistant instance.

    Public methods deliberately forward to ``ConnectionManager``,
    ``PropertyManager``, ``DeviceDiscoveryManager``, ``AvailabilityManager``,
    and optional cloud clients. Callers (coordinator, flows, entities) should
    keep using this type rather than reaching into managers. The wide surface
    is intentional glue, not a second business layer — prefer adding behaviour
    on the manager that owns it, then expose a thin method here if HA code
    needs it.
    """

    def __init__(
        self,
        host: IotHost,
        *,
        resolve_bindings: BindingResolver,
        default_port: int = DEFAULT_PORT,
        event_dispatcher: Callable[[str, dict[str, Any]], None] | None = None,
        device_auth: Callable[[str], tuple[str, int]] | None = None,
    ) -> None:
        """Initialize the ESP-Weaver API coordinator.

        Args:
            host: The application this runs in; see :class:`IotHost`.
            resolve_bindings: Turns a node's report into entity bindings.
            event_dispatcher: Called as ``(node_id, params)`` on every report.
            device_auth: Returns ``(pop, security_version)`` for a node.
        """
        self.host = host
        self.default_port = default_port
        self.registry = DeviceRegistry(default_port)

        self._availability = AvailabilityManager(host, self.registry, default_port)
        self._discovery = DeviceDiscoveryManager(host, self.registry, resolve_bindings)
        self._property = PropertyManager(
            self.registry,
            event_dispatcher=event_dispatcher,
        )
        self._connection = ConnectionManager(
            host,
            self.registry,
            message_handler_factory=self._active_report_handler,
            on_properties=self.publish_properties,
            device_auth=device_auth,
        )

        # Inject connection callbacks after all managers are initialized
        # This avoids circular dependency during initialization
        self._property.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=self._connect_and_sync_device,
                reconnect_and_retry_params=self._reconnect_and_retry_params,
            )
        )

        self._global_browser: ServiceBrowser | None = None
        self._global_listener: GlobalMDNSListener | None = None
        self._registering: dict[str, asyncio.Future[bool]] = {}

        # Cloud control: per-device client (supports mixed regions)
        self._cloud_clients: dict[str, RainMakerCLIClient] = {}
        # Mapping of node_id to connection_mode ("local" or "cloud")
        self._device_connection_modes: dict[str, str] = {}

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

    def is_discovery_completed(self, node_id: str) -> bool:
        """Check if entity discovery is completed for a device."""
        return self.registry.is_discovery_completed(node_id)

    def mark_discovery_completed(self, node_id: str) -> None:
        """Mark entity discovery as completed for a device."""
        self.registry.mark_discovery_completed(node_id)

    async def parse_and_discover_entities(self, node_id: str, properties: list) -> None:
        """Parse device configuration and trigger entity discovery."""
        await self._discovery.parse_and_discover_entities(node_id, properties)

    async def discover_from_cloud(
        self,
        node_id: str,
        node_config: dict[str, Any],
        cloud_params: dict[str, Any],
    ) -> None:
        """Discover entities from cloud device config."""
        await self._discovery.discover_from_cloud(node_id, node_config, cloud_params)

    async def publish_params(self, node_id: str, params: dict[str, Any]) -> None:
        """Hand a node's full report to its entities, and keep it as the latest.

        The one route a report takes, whether it was polled, read back on
        connecting, or pushed by the device. A write's echo does not come this
        way: it carries one parameter, not the node's state.
        """
        self._property.process_property_update(node_id, params)
        self._discovery.remember_report(node_id, params)

    async def publish_properties(
        self, node_id: str, properties: list[dict[str, Any]]
    ) -> None:
        """Publish the params a node's raw properties carry, if they carry any."""
        _LOGGER.debug("Raw properties for %s: %s", node_id, properties)
        if params := get_raw_params(properties):
            await self.publish_params(node_id, params)

    def _active_report_handler(
        self, node_id: str
    ) -> Callable[[Any, Any], Awaitable[None]]:
        """Return the client callback that publishes a node's active reports.

        Query responses are answered by their futures, not here.
        """

        async def on_device_message(msg_source: Any, data: Any) -> None:
            if msg_source != local_ctrl.MessageSource.ACTIVE_REPORT:
                return
            if isinstance(data, dict) and KEY_PROPERTIES in data:
                await self.publish_properties(node_id, data[KEY_PROPERTIES])
            else:
                _LOGGER.warning(
                    "Device %s ACTIVE_REPORT invalid format - data: %s", node_id, data
                )

        return on_device_message

    def get_firmware_version(self, node_id: str) -> str | None:
        """Return the firmware version the node gave in its config."""
        device = self.registry.get_device(node_id)
        return device.firmware_version if device else None

    def set_firmware_version(self, node_id: str, version: str) -> None:
        """Adopt a firmware version a later config read reported (after an OTA)."""
        device = self.registry.get_device(node_id)
        if device is not None:
            device.firmware_version = version

    async def set_params(self, node_id: str, params: dict[str, Any]) -> WriteOutcome:
        """Write a ``{device_name: {param: value}}`` payload, local or cloud.

        Returns:
            How it ended. The cloud client answers only yes or no, so a cloud
            write is never UNREACHABLE.
        """
        if not params:
            return WriteOutcome.REFUSED

        if self.is_cloud_device(node_id):
            client = self._get_cloud_client(node_id)
            if not client:
                _LOGGER.error("Cloud client not configured for %s", node_id)
                return WriteOutcome.UNREACHABLE
            if await client.set_params(node_id, params):
                return WriteOutcome.ACCEPTED
            return WriteOutcome.REFUSED

        return await self._property.set_params(node_id, params)

    def set_connection_mode(self, node_id: str, connection_mode: str) -> None:
        """Record how a node is reached, local control or the cloud."""
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
        """Connect to a device and record it, once however many callers ask.

        `process_properties` syncs entity state on reconnection; initial setup
        passes False because the coordinator does discovery itself.
        """
        if port is None:
            port = self.default_port

        device = self.registry.register_device(node_id, ip, port)

        # The lock cannot be held across connect_and_sync, which takes it too;
        # a second caller arriving in the gap waits on the in-flight future.
        async with self.registry.device_lock(node_id):
            client = self.registry.get_client(node_id)
            if client and await client.is_connected():
                device.registered = True
                device.last_success = asyncio.get_running_loop().time()
                return True
            inflight = self._registering.get(node_id)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                self._registering[node_id] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            return await inflight

        # Whatever leaves this block, the in-flight future is resolved, or
        # every later registration of this node would wait on it for good.
        success = False
        outcome: BaseException | None = None
        try:
            success, _ = await self._connect_and_sync_device(
                node_id,
                ip,
                port,
                process_properties=process_properties,
            )
        except BaseException as err:
            outcome = err
            raise
        finally:
            # Plain attribute writes; nothing here yields, so no lock is needed.
            device.registered = bool(success) and outcome is None
            if device.registered:
                device.last_success = asyncio.get_running_loop().time()
            if outcome is not None:
                _LOGGER.debug("Failed to register device %s: %s", node_id, outcome)
            self._settle_inflight(node_id, inflight, success, outcome)

        return bool(success)

    def _settle_inflight(
        self,
        node_id: str,
        inflight: asyncio.Future[bool],
        success: bool,
        outcome: BaseException | None,
    ) -> None:
        """Hand the owner's result to everyone waiting on the same registration.

        The future may already be cancelled by ``cleanup()``.
        """
        if self._registering.get(node_id) is inflight:
            self._registering.pop(node_id, None)
        if inflight.done():
            return
        if outcome is None:
            inflight.set_result(bool(success))
            return
        if isinstance(outcome, asyncio.CancelledError):
            inflight.cancel()
            return
        if isinstance(outcome, Exception):
            inflight.set_exception(outcome)
            # Read it back so that a failure nobody else was waiting on is not
            # reported by asyncio as an exception that was never retrieved.
            inflight.exception()
            return
        # KeyboardInterrupt and friends: nothing sensible to hand over.
        inflight.cancel()

    async def unregister_device(self, node_id: str) -> None:
        """Unregister and clean up a device."""
        self._device_connection_modes.pop(node_id, None)
        self._cloud_clients.pop(node_id, None)
        await self._connection.disconnect_device(node_id)
        self.registry.remove_device(node_id)

    async def cleanup(self) -> None:
        """Clean up API resources."""
        if self._global_browser:
            # Raises if the browser was already cancelled underneath us.
            with contextlib.suppress(RuntimeError):
                self._global_browser.cancel()
            self._global_browser = None

        if self._global_listener is not None:
            self._global_listener.cancel_pending()
            self._global_listener = None
        for pending in list(self._registering.values()):
            if not pending.done():
                pending.cancel()
        self._registering.clear()
        self._cloud_clients.clear()
        self._device_connection_modes.clear()

        await self._connection.disconnect_all()

    async def start_services(self) -> None:
        """Start browsing mDNS for nodes. Calling it again is a no-op."""
        if self._global_browser is not None:
            return

        try:
            self._global_listener = GlobalMDNSListener(self.host, api=self)
            zc: Zeroconf = await self.host.zeroconf()
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

    async def read_properties(self, node_id: str) -> list[dict[str, Any]] | None:
        """Read a local node's properties over its open session.

        Returns:
            What the node answered, or None if it could not be asked; the
            client then reports ``IotHost.connection_lost``.
        """
        client = self.registry.get_client(node_id)
        if client is None:
            return None
        return await client.get_property_values()

    def get_device(self, node_id: str) -> DeviceInfo | None:
        """Return what the registry holds about a node, or None if it is unknown."""
        return self.registry.get_device(node_id)

    def get_reported_config(self, node_id: str) -> tuple[dict, dict]:
        """Return the node config and params as the device last reported them.

        Returns:
            A ``(config, params)`` pair, both empty until discovery has run.
        """
        device = self.registry.get_device(node_id)
        if device is None:
            return {}, {}
        return device.raw_config, device.raw_params

    async def _connect_and_sync_device(
        self,
        node_id: str,
        ip: str,
        port: int,
        *,
        process_properties: bool = True,
    ) -> tuple[bool, list | None]:
        """Connect to a device, and on a reconnection also re-read its state."""
        success, properties = await self._connection.connect_and_sync(
            node_id,
            ip,
            port,
            process_properties=process_properties,
        )

        # Discovery only belongs to the reconnection path. During initial setup
        # the coordinator runs it, which is what process_properties=False says.
        if (
            process_properties
            and success
            and properties
            and not self.registry.is_discovery_completed(node_id)
        ):
            await self._discovery.parse_and_discover_entities(node_id, properties)
            self.registry.mark_discovery_completed(node_id)

        return (success, properties)

    async def _reconnect_and_retry_params(
        self, node_id: str, params: dict[str, Any]
    ) -> WriteOutcome:
        """Reconnect, sync entity states, then run the params write once more."""
        device = self.registry.get_device(node_id)
        if not device or not device.ip:
            _LOGGER.warning(
                "Cannot reconnect device %s: device info not found", node_id
            )
            return WriteOutcome.UNREACHABLE

        label = ", ".join(params)
        _LOGGER.debug("Reconnecting %s to retry writing %s", node_id, label)

        # Sync all entities with device state before executing user's command.
        success, _ = await self._connection.connect_and_sync(
            node_id,
            device.ip,
            device.port or self.default_port,
            process_properties=True,
        )
        if not success:
            _LOGGER.warning("Reconnection failed for %s, cannot retry write", node_id)
            return WriteOutcome.UNREACHABLE

        outcome = await self._property.retry_params(node_id, params)
        if outcome is not WriteOutcome.ACCEPTED:
            _LOGGER.error("Writing %s to %s failed after reconnection", label, node_id)
            return outcome

        device.last_success = asyncio.get_running_loop().time()
        _LOGGER.info("Writing %s to %s succeeded after reconnection", label, node_id)
        return outcome
