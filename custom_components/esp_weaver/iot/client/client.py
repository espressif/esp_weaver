# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0
"""ESP Local Control Client.

This module provides the client implementation for communicating with
ESP devices using the ESP Local Control protocol.
"""

import asyncio
from collections.abc import Callable
import contextlib
import logging
import socket
from typing import TYPE_CHECKING, Any

from ..local_ctrl import local_ctrl, local_ctrl_codec
from ..specs.device_specs import (
    CONFIG_PROPERTY_INDEX,
    DEFAULT_PORT,
    DEFAULT_SECURITY_MODE,
    PARAMS_PROPERTY_INDEX,
    PROPERTY_SET_TIMEOUT,
    QUERY_TIMEOUT,
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

HTTPMessageListener = local_ctrl.HTTPMessageListener
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
        """Initialize client with ESP device information.

        Args:
            node_id: Unique identifier for the ESP device.
            ip: IP address of the ESP device.
            port: Port number for ESP Local Control. Defaults to 8080.
            pop: Proof of Possession (security credentials). Optional.
            security_mode: Security mode (0=no security, 1=with security).
                Defaults to 1.
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
        self._http_listener: HTTPMessageListener | None = None
        self._message_callbacks: list[Callable] = []

    async def connect(self) -> bool:
        """Establish connection to ESP device."""
        async with self._connect_lock:
            try:
                # Check if already connected
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

                # Clean up any stale session state before reconnecting
                await self._cleanup_session()

                # Create transport
                service_name = f"{self.ip}:{self.port}"
                self.transport = await local_ctrl.get_transport(
                    sel_transport="http", service_name=service_name
                )

                if not self.transport:
                    _LOGGER.error("Failed to create transport for %s", service_name)
                    return False

                # Create security context
                self.security_ctx = local_ctrl.get_security(
                    secver=self.security_mode,
                    sec_patch_ver=0,
                    username="",
                    password="",
                    pop=self.pop or "",
                )

                if not self.security_ctx:
                    _LOGGER.error("Failed to create security context")
                    return False

                # Establish session with device
                established = await local_ctrl.establish_session(
                    self.transport, self.security_ctx
                )
                if not established:
                    _LOGGER.error("Failed to establish session with device")
                    return False

                # Reset connection state after handshake
                if hasattr(self.transport, "reset_connection"):
                    self.transport.reset_connection()

                self.session_established = True

                # Clear connection error flag on successful connect
                self._connection_error = False

                # Enable TCP keepalive for automatic connection monitoring
                await self._enable_tcp_keepalive()

                # Start HTTP message listener with callbacks already in place
                await self._start_message_listener()

                _LOGGER.info(
                    "Successfully established connection to device %s at %s:%s",
                    self.node_id,
                    self.ip,
                    self.port,
                )

            except (OSError, TimeoutError, ConnectionError, RuntimeError) as err:
                _LOGGER.warning(
                    "Connection failed for device %s at %s:%s: %s",
                    self.node_id,
                    self.ip,
                    self.port,
                    err,
                )
                await self.disconnect()
                return False

        return True

    async def is_connected(self) -> bool:
        """Check if connection is valid."""
        if self._connection_error:
            return False

        if not self.transport or not self.session_established:
            return False

        # Check socket health (TransportHTTP has is_socket_healthy method)
        check_health = getattr(self.transport, "is_socket_healthy", lambda: True)
        try:
            return bool(check_health())
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

    async def get_property_values(self) -> list[dict[str, Any]]:
        """Get all property values."""
        async with self._control_lock:
            try:
                # Use unified connection check
                if not await self._ensure_connected():
                    return []

                props = await self._get_all_property_values_via_listener(
                    timeout=QUERY_TIMEOUT
                )

                if not props:
                    _LOGGER.warning("No properties returned from device")

            except (OSError, TimeoutError, ConnectionError) as err:
                _LOGGER.error("Failed to get property values: %s", err)
                return []

        return props

    async def _get_all_property_values_via_listener(
        self, timeout: float = QUERY_TIMEOUT
    ) -> list[dict[str, Any]]:
        """Get all property values using HTTPMessageListener."""
        try:
            if (
                not self.transport
                or not self.session_established
                or not self._http_listener
                or not self.security_ctx
            ):
                return []

            get_count = local_ctrl_codec.get_prop_count_request
            count_request = get_count(self.security_ctx)

            send_wait = self._http_listener.send_query_and_wait
            result = await send_wait(self.transport, count_request, timeout=timeout)
            msg_source, count_data = result

            if msg_source != MessageSource.QUERY_RESPONSE:
                return []

            prop_count = parse_property_count_response(count_data)

            if prop_count <= 0:
                return []

            indices = list(range(prop_count))
            get_vals = local_ctrl_codec.get_prop_vals_request
            props_request = get_vals(self.security_ctx, indices)

            result = await send_wait(self.transport, props_request, timeout=timeout)
            msg_source, props_data = result

            if msg_source != MessageSource.QUERY_RESPONSE:
                return []

            return parse_property_values_response(props_data)

        except (OSError, TimeoutError, ConnectionError) as err:
            _LOGGER.error("Failed to get property values via listener: %s", err)
            return []

    async def set_property_values(self, indices: list[int], values: list[Any]) -> bool:
        """Set property values."""
        async with self._control_lock:
            try:
                if self._connection_error:
                    return False

                if not self.transport or not self.session_established:
                    return False

                esp_values = convert_values_to_esp_format(values)

                if indices and indices[0] == CONFIG_PROPERTY_INDEX:
                    indices = [PARAMS_PROPERTY_INDEX, *indices[1:]]

                try:
                    result = await asyncio.wait_for(
                        local_ctrl.set_property_values(
                            self.transport,
                            self.security_ctx,
                            props=None,
                            indices=indices,
                            values=esp_values,
                            check_readonly=False,
                            listener=self._http_listener,
                        ),
                        timeout=PROPERTY_SET_TIMEOUT,
                    )
                except TimeoutError:
                    _LOGGER.warning(
                        "Property setting timed out for device %s",
                        self.node_id,
                    )
                    self.mark_connection_error()
                    return False

                success: bool = bool(result)
                if not success:
                    _LOGGER.warning("Device rejected property setting request")
                return success  # noqa: TRY300

            except (OSError, ConnectionError, BrokenPipeError) as err:
                _LOGGER.error("Failed to set property values: %s", err)
                self.mark_connection_error()
                return False

    async def disconnect(self) -> None:
        """Disconnect from ESP device."""
        _LOGGER.debug("Disconnecting from device %s", self.node_id)
        self._connection_error = True
        await self._cleanup_session()
        _LOGGER.info("Disconnected from device %s", self.node_id)

    async def _cleanup_session(self) -> None:
        """Clean up session resources."""
        await self._stop_message_listener()

        if self.transport:
            # Close HTTP connection and send FIN packet
            # Suppress all errors during cleanup - connection may already be closed
            with contextlib.suppress(Exception):
                if hasattr(self.transport, "close"):
                    self.transport.close()
                elif hasattr(self.transport, "conn"):
                    self.transport.conn.close()
                await asyncio.sleep(SESSION_CLEANUP_DELAY)

            self.transport = None

        self.session_established = False
        self.security_ctx = None

    async def _enable_tcp_keepalive(self) -> None:
        """Enable TCP keepalive for connection monitoring."""
        try:
            if not self.transport or not hasattr(self.transport, "conn"):
                return

            conn = self.transport.conn
            if not conn or not hasattr(conn, "sock") or conn.sock is None:
                return

            sock = conn.sock

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
            self._http_listener = HTTPMessageListener(
                self.transport, self.security_ctx, client=self, verbose=False
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
