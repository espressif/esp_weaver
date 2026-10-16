# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Property management for ESP IoT devices."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from typing import TYPE_CHECKING, Any

from ..specs.device_specs import PARAMS_PROPERTY_INDEX

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)


class WriteOutcome(StrEnum):
    """How a parameter write ended, as far as the caller needs to know.

    The client reports a network failure and a refusal alike, as ``False``;
    this layer tells them apart, so that the entity can say "could not reach
    the device" rather than "the device refused" when the node is offline.
    """

    ACCEPTED = "accepted"
    # The device answered, and said no.
    REFUSED = "refused"
    # The device could not be asked: no session, and none could be opened.
    UNREACHABLE = "unreachable"


@dataclass
class ConnectionCallbacks:
    """How the property manager reaches the connection layer.

    ``establish_connection(node_id, ip, port)`` opens a session and answers
    ``(success, properties)``; ``reconnect_and_retry_params(node_id, params)``
    rebuilds the session and runs one write over it.
    """

    establish_connection: Callable[
        [str, str, int], Coroutine[Any, Any, tuple[bool, list | None]]
    ]
    reconnect_and_retry_params: Callable[[str, dict], Coroutine[Any, Any, WriteOutcome]]


class PropertyManager:
    """Manages device property operations and message handling."""

    def __init__(
        self,
        registry: "DeviceRegistry",
        event_dispatcher: Callable[[str, dict], None] | None = None,
    ) -> None:
        """Initialize the property manager."""
        self.registry = registry
        self._event_dispatcher = event_dispatcher
        self._connection_callbacks: ConnectionCallbacks | None = None

    def set_connection_callbacks(self, callbacks: ConnectionCallbacks) -> None:
        """Set connection callbacks after initialization."""
        self._connection_callbacks = callbacks

    async def set_params(self, node_id: str, params: dict[str, Any]) -> WriteOutcome:
        """Write a RainMaker params payload verbatim.

        Returns:
            How it ended; a write the device never heard of is UNREACHABLE,
            not REFUSED.
        """
        if not params:
            return WriteOutcome.REFUSED

        if not self.registry.has_client(node_id):
            if not await self._establish_connection_for_set(node_id):
                return WriteOutcome.UNREACHABLE

        outcome = await self._send_params(node_id, params)
        if outcome is None:
            return await self._reconnect_and_retry_params(node_id, params)
        if outcome is WriteOutcome.ACCEPTED:
            self._echo(node_id, params)
        return outcome

    async def retry_params(self, node_id: str, params: dict[str, Any]) -> WriteOutcome:
        """Run a params write once more after the connection was rebuilt. Once.

        Returns:
            How it ended; a session that dropped again is UNREACHABLE.
        """
        outcome = await self._send_params(node_id, params)
        if outcome is None:
            return WriteOutcome.UNREACHABLE
        if outcome is WriteOutcome.ACCEPTED:
            self._echo(node_id, params)
        return outcome

    def _echo(self, node_id: str, params: dict[str, Any]) -> None:
        """Report a write back as if the device had, so the entity confirms it."""
        if self._event_dispatcher:
            self._event_dispatcher(node_id, params)

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

    async def _reconnect_and_retry_params(
        self, node_id: str, params: dict[str, Any]
    ) -> WriteOutcome:
        """Reconnect and retry a params write."""
        if not self._connection_callbacks:
            _LOGGER.warning("No connection callbacks configured for reconnect")
            return WriteOutcome.UNREACHABLE

        return await self._connection_callbacks.reconnect_and_retry_params(
            node_id, params
        )

    async def _send_params(
        self, node_id: str, params: dict[str, Any]
    ) -> WriteOutcome | None:
        """Send ``{device_name: {param: value}}`` over local control, once.

        Returns:
            How it ended, or None if the session dropped under the write and
            a reconnection may still carry it.
        """
        client = self.registry.get_client(node_id)
        if client is None:
            return WriteOutcome.UNREACHABLE

        # What to name in log messages: the device names the payload touches.
        label = ", ".join(params)

        try:
            command_str = json.dumps(params)
        except (ValueError, TypeError):
            _LOGGER.exception("Invalid property parameters for node_id=%s", node_id)
            return WriteOutcome.REFUSED

        # The client answers False for a network error (marking the connection
        # bad) and for a refused write alike; the connection tells them apart.
        if await client.set_property_values([PARAMS_PROPERTY_INDEX], [command_str]):
            _LOGGER.debug("Wrote %s to device %s: %s", label, node_id, params)
            return WriteOutcome.ACCEPTED

        if not await client.is_connected():
            _LOGGER.warning(
                "Connection to %s lost while writing %s, will reconnect",
                node_id,
                label,
            )
            return None

        _LOGGER.warning("Device %s refused the write to %s", node_id, label)
        return WriteOutcome.REFUSED

    def process_property_update(self, node_id: str, params_data: dict) -> None:
        """Hand a params payload to the entities, as sent."""
        if self._event_dispatcher:
            self._event_dispatcher(node_id, params_data)
