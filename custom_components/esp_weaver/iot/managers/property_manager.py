# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Property management for ESP IoT devices."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from ..client.client import ESPLocalCtrlClient
from ..local_ctrl import local_ctrl
from ..parsers.property_parser import get_raw_params
from ..payload.command_builder import build_device_command
from ..specs.device_specs import PARAMS_PROPERTY_INDEX, PROPERTY_SET_TIMEOUT
from ..specs.keys import KEY_PROPERTIES

_RESULT_SUCCESS = "success"
_RESULT_RECONNECT = "reconnect"
_RESULT_FAILED = "failed"

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

EventDispatcherCallback = Callable[[str, dict], None]
EstablishConnectionCallback = Callable[
    [str, str, int], Coroutine[Any, Any, tuple[bool, list | None]]
]
ReconnectAndRetryCallback = Callable[[str, str, Any], Coroutine[Any, Any, bool]]


@dataclass
class ConnectionCallbacks:
    """Container for connection-related callbacks."""

    establish_connection: EstablishConnectionCallback
    reconnect_and_retry: ReconnectAndRetryCallback


class PropertyManager:
    """Manages device property operations and message handling."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        registry: "DeviceRegistry",
        event_dispatcher: EventDispatcherCallback | None = None,
        connection_callbacks: ConnectionCallbacks | None = None,
    ) -> None:
        """Initialize the property manager."""
        self.hass = hass
        self.domain = domain
        self.registry = registry
        self._event_dispatcher = event_dispatcher
        self._connection_callbacks = connection_callbacks

    def set_connection_callbacks(self, callbacks: ConnectionCallbacks) -> None:
        """Set connection callbacks after initialization."""
        self._connection_callbacks = callbacks

    async def set_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> bool:
        """Set ESP device property value."""
        if not self.registry.has_client(node_id):
            if not await self._establish_connection_for_set(node_id):
                return False

        result = await self._try_set_property(node_id, prop_name, value)

        if result == _RESULT_SUCCESS:
            # Fire update event so entity state is confirmed
            self._fire_property_update_event(node_id, prop_name, value)
            return True
        if result == _RESULT_RECONNECT:
            return await self._reconnect_and_retry(node_id, prop_name, value)

        return False

    async def _establish_connection_for_set(self, node_id: str) -> bool:
        """Establish connection for property set operation."""
        if not self._connection_callbacks:
            _LOGGER.warning("No connection callbacks configured for property set")
            return False

        device = self.registry.get_device(node_id)
        if not device:
            return False

        port = device.port
        ip = device.ip

        if not ip or not port:
            _LOGGER.warning(
                "Device %s missing IP or port (ip=%s, port=%s)",
                node_id,
                ip,
                port,
            )
            return False

        success, _ = await self._connection_callbacks.establish_connection(
            node_id, ip, port
        )
        return success

    async def _reconnect_and_retry(
        self, node_id: str, prop_name: str, value: Any
    ) -> bool:
        """Reconnect and retry property set."""
        if not self._connection_callbacks:
            _LOGGER.warning("No connection callbacks configured for reconnect")
            return False

        return await self._connection_callbacks.reconnect_and_retry(
            node_id, prop_name, value
        )

    async def _try_set_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> str:
        """Try to set property value on device."""
        client = self.registry.get_client(node_id)
        if not client or not isinstance(client, ESPLocalCtrlClient):
            return _RESULT_FAILED

        try:
            # Build ESP-RainMaker JSON command based on parameter type
            json_command = build_device_command(prop_name, value)
            if not json_command:
                _LOGGER.error("Failed to build command for parameter: %s", prop_name)
                return _RESULT_FAILED

            command_str = json.dumps(json_command)

            # Use params property for ESP-RainMaker devices
            success = await asyncio.wait_for(
                client.set_property_values([PARAMS_PROPERTY_INDEX], [command_str]),
                timeout=PROPERTY_SET_TIMEOUT,
            )

        except TimeoutError:
            _LOGGER.warning(
                "Property set timeout for device %s (prop=%s), will attempt reconnect",
                node_id,
                prop_name,
            )
            return _RESULT_RECONNECT

        except (OSError, ConnectionError) as err:
            # Network-related errors - connection likely lost, try reconnect
            _LOGGER.warning(
                "Network error setting property for device %s (prop=%s): %s, "
                "will attempt reconnect",
                node_id,
                prop_name,
                err,
            )
            return _RESULT_RECONNECT

        except (ValueError, TypeError, KeyError):
            _LOGGER.exception("Invalid property parameters for node_id=%s", node_id)
            return _RESULT_FAILED

        if success:
            self._update_stored_property(node_id, prop_name, value)
            _LOGGER.debug(
                "Property '%s' set successfully for device %s (value=%s)",
                prop_name,
                node_id,
                value,
            )
            return _RESULT_SUCCESS

        _LOGGER.warning(
            "Property set failed for device %s (prop=%s), will attempt reconnect",
            node_id,
            prop_name,
        )
        return _RESULT_RECONNECT

    def _update_stored_property(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> None:
        """Update stored property value."""
        device = self.registry.get_device(node_id)
        if device:
            device.properties[prop_name] = value

    def _fire_property_update_event(
        self,
        node_id: str,
        prop_name: str,
        value: Any,
    ) -> None:
        """Fire update event after successful property set."""
        if not self._event_dispatcher:
            return

        # Build the same command format used for sending to device
        params_data = build_device_command(prop_name, value)
        if params_data:
            self._event_dispatcher(node_id, params_data)

    def process_property_update(self, node_id: str, params_data: dict) -> None:
        """Process property update and fire events."""
        if self._event_dispatcher:
            self._event_dispatcher(node_id, params_data)

    def create_message_handler(
        self,
        node_id: str,
        process_property_update: Callable[[str, dict], None] | None = None,
    ) -> Callable:
        """Create message handler for a specific device."""
        update_callback = process_property_update or self.process_property_update

        async def on_device_message(msg_source: Any, data: Any) -> None:
            """Handle incoming message from ESP device."""
            if msg_source == local_ctrl.MessageSource.ACTIVE_REPORT:
                if isinstance(data, dict) and KEY_PROPERTIES in data:
                    await self.process_active_report(
                        node_id, data[KEY_PROPERTIES], update_callback
                    )
                else:
                    _LOGGER.warning(
                        "Device %s ACTIVE_REPORT invalid format - data: %s",
                        node_id,
                        data,
                    )

            elif msg_source == local_ctrl.MessageSource.QUERY_RESPONSE:
                pass  # Handled via futures in HTTPMessageListener

        return on_device_message

    async def process_active_report(
        self,
        node_id: str,
        properties: list[dict[str, Any]],
        process_property_update: (
            Callable[[str, dict[str, Any]], None | Coroutine[Any, Any, None]] | None
        ) = None,
    ) -> None:
        """Process active report property values and fire update events."""
        _LOGGER.debug("Active report raw properties for %s: %s", node_id, properties)
        params_data = get_raw_params(properties)

        if params_data:
            update_callback = process_property_update or self.process_property_update
            result = update_callback(node_id, params_data)
            if asyncio.iscoroutine(result):
                await result
