# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""One connection to one ESP device, over the ESP Local Control protocol."""

import asyncio
from collections.abc import Callable
import contextlib
import logging
import socket
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag
from google.protobuf.message import DecodeError

from ..local_ctrl import local_ctrl, local_ctrl_codec
from ..local_ctrl.const import QUERY_TIMEOUT
from ..specs.device_specs import (
    DEFAULT_PORT,
    DEFAULT_SECURITY_MODE,
    PROPERTY_SET_TIMEOUT,
    SESSION_CLEANUP_DELAY,
    TCP_KEEPCNT,
    TCP_KEEPIDLE,
    TCP_KEEPINTVL,
)
from .client_utils import (
    convert_values_to_esp_format,
    parse_property_count_response,
    parse_property_values_response,
)

if TYPE_CHECKING:
    from ..local_ctrl.transport import Transport

_LOGGER = logging.getLogger(__name__)

MessageSource = local_ctrl.MessageSource


class ESPLocalCtrlClient:
    """ESP Local Control client for communicating with ESP devices."""

    def __init__(
        self,
        node_id: str,
        ip: str,
        *,
        port: int = DEFAULT_PORT,
        pop: str | None = None,
        security_mode: int = DEFAULT_SECURITY_MODE,
    ) -> None:
        """Initialize a client for one device, with the credentials it expects.

        `security_mode` is what the device reported: 0 for none, 1 for a PoP,
        2 for SRP6a. `pop` is unused at 0 and required otherwise.
        """
        self.node_id = node_id
        self.ip = ip
        self.port = port
        self.pop = pop
        self.security_mode = security_mode

        self.transport: Transport | None = None
        self.security_ctx: Any | None = None
        self.session_established: bool = False

        self._control_lock: asyncio.Lock = asyncio.Lock()
        self._connect_lock: asyncio.Lock = asyncio.Lock()

        self._connection_error: bool = False
        self._connection_error_callback: Callable[[str], None] | None = None
        self._http_listener: local_ctrl.HTTPMessageListener | None = None
        self._message_callbacks: list[Callable] = []

    async def connect(self) -> bool:
        """Establish connection to ESP device."""
        async with self._connect_lock:
            try:
                if await self.is_connected():
                    _LOGGER.debug(
                        "Device %s already connected, reusing connection",
                        self.node_id,
                    )
                    return True

                _LOGGER.debug(
                    "Initiating connection to device %s at %s:%s",
                    self.node_id,
                    self.ip,
                    self.port,
                )
                return await self._open_session()

            # Malformed key (ValueError), bad frame (DecodeError), wrong PoP
            # (InvalidTag): connect() reports failure rather than raising.
            except (OSError, RuntimeError, ValueError, DecodeError, InvalidTag) as err:
                _LOGGER.warning(
                    "Connection failed for device %s at %s:%s: %s",
                    self.node_id,
                    self.ip,
                    self.port,
                    err,
                )
                await self.disconnect()
                return False

    async def _open_session(self) -> bool:
        """Build a transport, handshake over it, and start listening on it.

        Returns:
            True once the device is ready to be asked for its properties.
        """
        # Clean up any stale session state before reconnecting
        await self._cleanup_session()

        service_name = f"{self.ip}:{self.port}"
        self.transport = await local_ctrl.get_transport(
            sel_transport="http", service_name=service_name
        )
        if not self.transport:
            _LOGGER.error("Failed to create transport for %s", service_name)
            return False

        self.security_ctx = await local_ctrl.build_security(
            self.transport, self.security_mode, self.pop or ""
        )
        if not self.security_ctx:
            _LOGGER.error("Failed to create security context")
            return False

        if not await local_ctrl.establish_session(self.transport, self.security_ctx):
            _LOGGER.error("Failed to establish session with device")
            return False

        # Reset connection state after handshake
        self.transport.reset_connection()

        self.session_established = True
        self._connection_error = False

        # Keepalive watches the connection; the listener is started with the
        # callbacks already in place so no report can arrive unheard.
        await self._enable_tcp_keepalive()
        await self._start_message_listener()

        _LOGGER.info(
            "Successfully established connection to device %s at %s:%s",
            self.node_id,
            self.ip,
            self.port,
        )
        return True

    @property
    def has_session(self) -> bool:
        """Whether a session was established and its transport is still held.

        Bookkeeping only; ``is_connected`` is what asks the socket.
        """
        return self.session_established and self.transport is not None

    async def is_connected(self) -> bool:
        """Check if connection is valid."""
        if self._connection_error:
            return False

        transport = self.transport
        if transport is None or not self.session_established:
            return False

        try:
            return transport.is_socket_healthy()
        except OSError:
            return False

    async def _ensure_connected(self) -> bool:
        """Ensure device is connected, reconnect if necessary."""
        if self._connection_error:
            _LOGGER.warning(
                "Connection error detected, forcing reconnection for device %s",
                self.node_id,
            )
            # connect() handles cleanup internally under _connect_lock
            return await self.connect()

        if not self.transport or not self.session_established:
            return await self.connect()

        return True

    async def get_property_values(self) -> list[dict[str, Any]] | None:
        """Read every property the device has.

        Returns:
            The properties, or None if the device could not be asked; the
            connection is then marked bad.
        """
        async with self._control_lock:
            if not await self._ensure_connected():
                return None
            try:
                return await self._get_all_property_values_via_listener(
                    timeout=QUERY_TIMEOUT
                )
            except (OSError, RuntimeError) as err:
                # RuntimeError is how the transport reports a failed request.
                _LOGGER.error("Failed to get property values: %s", err)
                self.mark_connection_error()
                return None

    async def _get_all_property_values_via_listener(
        self, timeout: float = QUERY_TIMEOUT
    ) -> list[dict[str, Any]] | None:
        """Ask for the property count, then for every property.

        Returns:
            The properties, or None if either query went unanswered; the
            connection is then marked bad.
        """
        if (
            not self.transport
            or not self.session_established
            or not self._http_listener
            or not self.security_ctx
        ):
            return None

        send_wait = self._http_listener.send_query_and_wait
        count_request = local_ctrl_codec.get_prop_count_request(self.security_ctx)
        msg_source, count_data = await send_wait(
            self.transport, count_request, timeout=timeout
        )
        if msg_source != MessageSource.QUERY_RESPONSE:
            _LOGGER.warning("Device %s did not answer a property query", self.node_id)
            self.mark_connection_error()
            return None

        prop_count = parse_property_count_response(count_data)
        if prop_count <= 0:
            return []

        props_request = local_ctrl_codec.get_prop_vals_request(
            self.security_ctx, list(range(prop_count))
        )
        msg_source, props_data = await send_wait(
            self.transport, props_request, timeout=timeout
        )
        if msg_source != MessageSource.QUERY_RESPONSE:
            _LOGGER.warning("Device %s did not answer a property query", self.node_id)
            self.mark_connection_error()
            return None

        return parse_property_values_response(props_data)

    async def set_property_values(self, indices: list[int], values: list[Any]) -> bool:
        """Write property values.

        Returns:
            True if the device accepted. False if it refused, or if it could
            not be asked; the connection is then marked bad.
        """
        async with self._control_lock:
            if self._connection_error:
                return False

            if not self.transport or not self.session_established:
                return False

            esp_values = convert_values_to_esp_format(values)

            try:
                # local_ctrl is the vendored protocol library and is excluded
                # from type checking; see pyproject.toml.
                result = await local_ctrl.set_property_values(  # type: ignore[no-untyped-call]
                    self.transport,
                    self.security_ctx,
                    indices=indices,
                    values=esp_values,
                    listener=self._http_listener,
                    timeout=PROPERTY_SET_TIMEOUT,
                )
            except (OSError, RuntimeError) as err:
                _LOGGER.error("Failed to set property values: %s", err)
                self.mark_connection_error()
                return False

            # No answer within the timeout: the session is not to be trusted.
            if result is None:
                _LOGGER.warning(
                    "Property setting timed out for device %s", self.node_id
                )
                self.mark_connection_error()
                return False

            if not result:
                _LOGGER.warning("Device rejected property setting request")
            return bool(result)

    async def disconnect(self) -> None:
        """Disconnect from ESP device."""
        _LOGGER.debug("Disconnecting from device %s", self.node_id)
        self._connection_error = True
        await self._cleanup_session()
        _LOGGER.debug("Disconnected from device %s", self.node_id)

    async def _cleanup_session(self) -> None:
        """Clean up session resources."""
        await self._stop_message_listener()

        if self.transport:
            # The connection may already be gone; closing it a second time is
            # not an error worth hearing about during teardown.
            with contextlib.suppress(OSError, RuntimeError):
                self.transport.close()
            await asyncio.sleep(SESSION_CLEANUP_DELAY)

            self.transport = None

        self.session_established = False
        self.security_ctx = None

    async def _enable_tcp_keepalive(self) -> None:
        """Enable TCP keepalive for connection monitoring."""
        try:
            if not self.transport:
                return

            sock = self.transport.socket()
            if sock is None:
                return

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, TCP_KEEPIDLE)
            if hasattr(socket, "TCP_KEEPINTVL"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, TCP_KEEPINTVL)
            if hasattr(socket, "TCP_KEEPCNT"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, TCP_KEEPCNT)

        except OSError as err:
            _LOGGER.debug("Failed to enable TCP keepalive: %s", err)

    async def _start_message_listener(self) -> None:
        """Start HTTP message listener for active reports and responses."""
        try:
            if self._http_listener is not None:
                return

            if not self.transport or not self.security_ctx:
                return

            # Create and start listener
            self._http_listener = local_ctrl.HTTPMessageListener(
                self.transport, self.security_ctx, client=self
            )

            for callback in self._message_callbacks:
                self._http_listener.add_callback(callback)

            await self._http_listener.start()

        except asyncio.CancelledError:
            self._http_listener = None
            raise
        except (OSError, RuntimeError) as err:
            _LOGGER.error("Failed to start HTTP message listener: %s", err)
            self._http_listener = None

    async def _stop_message_listener(self) -> None:
        """Stop HTTP message listener."""
        try:
            if self._http_listener:
                await self._http_listener.stop()
                self._http_listener = None
        except asyncio.CancelledError:
            self._http_listener = None
            raise
        except (OSError, RuntimeError):
            self._http_listener = None

    def mark_connection_error(self) -> None:
        """Mark connection as having an error."""
        if self._connection_error:
            return

        self._connection_error = True
        _LOGGER.warning("Connection error for device %s", self.node_id)

        if self._connection_error_callback:
            with contextlib.suppress(TypeError, ValueError):
                self._connection_error_callback(self.node_id)

    def set_connection_error_callback(self, callback: Callable[[str], None]) -> None:
        """Set callback to be invoked when connection error is detected."""
        self._connection_error_callback = callback

    def add_message_callback(self, callback: Callable) -> None:
        """Register a callback to be invoked when HTTP messages are received."""
        if callback not in self._message_callbacks:
            self._message_callbacks.append(callback)

            listener = self._http_listener
            if listener is not None:
                listener.add_callback(callback)
