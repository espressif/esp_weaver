#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""ESP Local Control module.

This module provides the main API for ESP Local Control protocol,
including property management, transport handling, and message parsing.
"""

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from . import local_ctrl_codec, security
from .const import (
    FIELD_ERROR,
    FIELD_STATUS,
    HEADER_CONTENT_LENGTH,
    HTTP_HEADER_MIN_LENGTH,
    LISTENER_ERROR_SLEEP,
    LISTENER_RECV_TIMEOUT,
    QUERY_TIMEOUT,
    SECURITY_MODE_SRP6A,
    SOCKET_RECV_BUFFER_SIZE,
    SRP6A_USERNAME,
)

if TYPE_CHECKING:
    from .security.security import Security
    from .transport.transport import Transport

_LOGGER = logging.getLogger(__name__)


async def build_security(
    tp: "Transport", secver: int, pop: str
) -> "security.Security | None":
    """Return the crypto that matches the firmware on the other end.

    For both sec1 and sec2 the device's protocomm patch version is read first,
    from the unencrypted /version endpoint. On sec1 patch_ver 0 means a shared
    TX/RX AES-CTR keystream (stock, e.g. RainMaker) and 1 means separate ones
    (esp_weaver); sec2 uses it to pick the AES-GCM IV variant. sec2 (SRP6a)
    authenticates as a fixed username with the PoP as password; sec1 takes the
    PoP directly.

    This is the one place credentials are turned into a context, so the config
    flow's PoP test and the runtime connection cannot hand a device different
    ones.

    Returns:
        The context, or None for a security version this does not speak.
    """
    sec_patch_ver = 0
    if secver >= 1:
        sec_patch_ver = await get_sec_patch_ver(tp) or 0
        _LOGGER.debug("Device reports sec_ver=%s sec_patch_ver=%s", secver, sec_patch_ver)

    if secver == SECURITY_MODE_SRP6A:
        return security.Security2(sec_patch_ver, SRP6A_USERNAME, pop)
    if secver == 1:
        return security.Security1(pop, sec_patch_ver=sec_patch_ver)
    if secver == 0:
        return security.Security0()
    return None


async def get_transport(sel_transport: str, service_name: str):
    """Create transport for the specified protocol."""
    try:
        tp = None
        if sel_transport == "http":
            # Local import to avoid circular dependency
            from .transport import TransportHTTP

            loop = asyncio.get_running_loop()
            tp = await loop.run_in_executor(None, TransportHTTP, service_name)
        else:
            _LOGGER.warning("Unsupported transport type: %s", sel_transport)
        return tp
    except RuntimeError as e:
        _LOGGER.error("Failed to create %s transport: %s", sel_transport, e)
        return None


async def version_info(tp: "Transport") -> dict[str, Any] | None:
    """Ask the ``esp_local_ctrl/version`` endpoint and return its ``local_ctrl`` block.

    The endpoint is unencrypted, so it can be read before any session exists.

    Returns:
        The block, empty if the answer was not JSON, or None if the device could
        not be asked.
    """
    try:
        response = await tp.send_data("esp_local_ctrl/version", "---")
    except RuntimeError as err:
        _LOGGER.error("Failed to read esp_local_ctrl/version: %s", err)
        return None
    try:
        info = json.loads(response)
    except ValueError:
        return {}
    block = info.get("local_ctrl", {}) if isinstance(info, dict) else {}
    return block if isinstance(block, dict) else {}


async def get_sec_patch_ver(tp: "Transport") -> int | None:
    """Get security patch version from device, 0 if it does not state one."""
    info = await version_info(tp)
    if info is None:
        return None
    return info.get("sec_patch_ver", 0)


MAX_HANDSHAKE_ATTEMPTS = 10


async def establish_session(tp: "Transport", sec: "Security") -> bool:
    """Establish security session with device."""
    try:
        response = None
        attempts = 0
        while True:
            attempts += 1
            if attempts > MAX_HANDSHAKE_ATTEMPTS:
                _LOGGER.error(
                    "Session handshake exceeded %d attempts", MAX_HANDSHAKE_ATTEMPTS
                )
                return False
            request = sec.security_session(response)
            if request is None:
                break
            response = await tp.send_data("esp_local_ctrl/session", request)
            if response is None:
                return False
        return True
    except RuntimeError as e:
        _LOGGER.error("Security handshake failed: %s", e)
        return False


async def set_property_values(
    tp,
    security_ctx,
    indices,
    values,
    *,
    listener=None,
    timeout=QUERY_TIMEOUT,
):
    """Set property values on device.

    Args:
        tp: Transport instance.
        security_ctx: Security context for encryption.
        indices: List of property indices to set.
        values: List of values to set.
        listener: HTTPMessageListener for async message handling (required).
        timeout: Seconds to wait for the device's answer.

    Returns:
        True if the device accepted, False if it refused or the request could
        not be sent, None if it gave no answer within ``timeout``.
    """
    try:
        message = local_ctrl_codec.set_prop_vals_request(security_ctx, indices, values)

        if not listener:
            _LOGGER.warning("set_property_values called without listener")
            return False

        msg_source, parsed_data = await listener.send_query_and_wait(
            tp, message, timeout=timeout
        )
        if msg_source is None:
            return None
        return parsed_data.get(FIELD_STATUS, 0) == 0
    except RuntimeError as e:
        _LOGGER.error("Failed to set property values: %s", e)
        return False


class MessageSource:
    """Enum for message source types."""

    ACTIVE_REPORT = "active_report"
    QUERY_RESPONSE = "query_response"
    UNKNOWN = "unknown"


def parse_http_headers(raw_data):
    """Parse HTTP headers from raw data.

    Args:
        raw_data: Raw bytes containing HTTP response.

    Returns:
        Tuple of (status_line, headers, payload, msg_source, offset).
    """
    try:
        if len(raw_data) < HTTP_HEADER_MIN_LENGTH:
            return (None, None, None, None, 0)

        prefix_offset = 0
        prefix = raw_data[:20].decode("ascii", errors="ignore")
        if not (prefix.startswith("HTTP/") or prefix.startswith("EVENT/")):
            for pattern in [b"EVENT/", b"HTTP/"]:
                pos = raw_data.find(pattern)
                if pos > 0:
                    prefix_offset = pos
                    raw_data = raw_data[pos:]
                    break
            else:
                return (None, None, None, None, 0)

        sep_index = raw_data.find(b"\r\n\r\n")
        if sep_index == -1:
            return (None, None, None, None, 0)

        headers_raw = raw_data[:sep_index].decode("latin-1")
        payload = raw_data[sep_index + 4 :]

        lines = headers_raw.split("\r\n")
        status_line = lines[0]

        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()

        message_source = MessageSource.UNKNOWN

        if "EVENT/1.0" in status_line:
            message_source = MessageSource.ACTIVE_REPORT
        elif "HTTP/1.1" in status_line:
            message_source = MessageSource.QUERY_RESPONSE

        return (
            status_line,
            headers,
            payload,
            message_source,
            prefix_offset + sep_index + 4,
        )

    except (ValueError, UnicodeDecodeError) as err:
        _LOGGER.debug("Failed to parse HTTP headers: %s", err)
        return (None, None, None, None, 0)


class ConnectionOwner(Protocol):
    """What the listener needs of whoever owns the connection it reads."""

    @property
    def node_id(self) -> str:
        """The node the connection belongs to, for the log."""

    def mark_connection_error(self) -> None:
        """Take note that the connection has been lost."""


class HTTPMessageListener:
    """Listener for HTTP messages from ESP device."""

    def __init__(
        self,
        transport: "Transport",
        security_ctx: "Security",
        client: ConnectionOwner | None = None,
    ) -> None:
        """Initialize HTTP message listener.

        Args:
            transport: Transport instance.
            security_ctx: Security context for decryption.
            client: Whoever owns the connection, told when it is lost.
        """
        self.transport = transport
        self.security_ctx = security_ctx
        self.client = client
        self.callbacks: list[Callable[..., Any]] = []
        self._running = False
        self._listen_task: asyncio.Task[None] | None = None
        self._buffer = bytearray()
        self._buffer_lock = asyncio.Lock()
        # Queries go one at a time, so there is at most one answer awaited.
        self._query_lock = asyncio.Lock()
        self._pending_query: asyncio.Future[Any] | None = None
        self._query_counter = 0
        self._first_message_processed = False

    def add_callback(self, callback: Callable[..., Any]) -> None:
        """Add callback for message notifications."""
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    async def send_query_and_wait(
        self,
        transport: "Transport",
        query_request: str,
        timeout: float = QUERY_TIMEOUT,
    ) -> tuple[Any, Any]:
        """Send query and wait for response."""
        async with self._query_lock:
            self._query_counter += 1
            query_id = f"query_{self._query_counter}"

            response_future: asyncio.Future[Any] = asyncio.Future()
            self._pending_query = response_future

            try:
                await transport.send_data_no_response(
                    "esp_local_ctrl/control", query_request
                )
                msg_source, data = await asyncio.wait_for(
                    response_future, timeout=timeout
                )
                return (msg_source, data)
            except TimeoutError as e:
                _LOGGER.debug(
                    "Query %s timed out after %s seconds: %s", query_id, timeout, e
                )
                return (None, None)
            finally:
                if self._pending_query is response_future:
                    self._pending_query = None

    async def start(self) -> bool:
        """Start listening for messages."""
        if self._running:
            return False
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        return True

    async def stop(self) -> None:
        """Stop listening for messages."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        # Clear callbacks to prevent memory leaks from closure references
        self.callbacks.clear()

        # Answer whatever is still waiting the same way a timeout would, rather
        # than dropping the future and leaving the caller to wait one out
        # against a listener that has already stopped.
        self._answer_pending_query((None, None))

    def _answer_pending_query(self, answer: tuple[Any, Any]) -> bool:
        """Hand an answer to the query waiting for one, if there is one.

        Returns:
            True if a query was waiting and has been answered.
        """
        future, self._pending_query = self._pending_query, None
        if future is None or future.done():
            return False
        future.set_result(answer)
        return True

    def _fail_connection(self) -> None:
        """Tell the client the connection this listener was reading is gone."""
        if self.client is not None:
            self.client.mark_connection_error()

    async def _listen_loop(self):
        """Main listening loop.

        Whatever ends this loop, ``_running`` is False afterwards and the client
        has been told, so a listener that died is never mistaken for one that is
        still reading. Without that, a decode error that slipped past the
        handlers left the connection reporting healthy while every query timed
        out against a listener that no longer existed.
        """
        try:
            await self._listen()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - task boundary; the failure is reported through the client  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Message listener for node %s stopped on an unexpected error",
                self.client.node_id if self.client else "unknown",
            )
            self._fail_connection()
        finally:
            self._running = False

    async def _listen(self):
        """Read frames until the connection ends or the listener is stopped."""
        while self._running:
            try:
                sock = self._get_socket()
                if not sock:
                    _LOGGER.warning(
                        "HTTPMessageListener: socket is None, exiting listen loop, "
                        "node_id=%s",
                        self.client.node_id if self.client else "unknown",
                    )
                    self._fail_connection()
                    break

                try:
                    # The timeout belongs on the socket, not on the await.
                    # Cancelling a wait_for does not cancel the recv underneath
                    # it: the worker thread stays parked until the device sends
                    # something, and there is one of these per connected device
                    # in an executor Home Assistant shares with everything else.
                    # A socket timeout is what actually gets the thread back.
                    if sock.gettimeout() != LISTENER_RECV_TIMEOUT:
                        sock.settimeout(LISTENER_RECV_TIMEOUT)
                    loop = asyncio.get_running_loop()
                    data = await loop.run_in_executor(
                        None, sock.recv, SOCKET_RECV_BUFFER_SIZE
                    )
                except TimeoutError:
                    continue
                except OSError as err:
                    _LOGGER.warning(
                        "HTTPMessageListener: connection error in recv for node %s: %s",
                        self.client.node_id if self.client else "unknown",
                        err,
                    )
                    self._fail_connection()
                    break

                if not data:
                    _LOGGER.warning(
                        "HTTPMessageListener: connection closed by peer, node_id=%s",
                        self.client.node_id if self.client else "unknown",
                    )
                    self._fail_connection()
                    break

                async with self._buffer_lock:
                    self._buffer.extend(data)

                if not await self._process_buffer():
                    break

            except OSError as err:
                _LOGGER.debug(
                    "HTTPMessageListener: connection error in listen loop: %s", err
                )
                await asyncio.sleep(LISTENER_ERROR_SLEEP)

    async def _process_buffer(self) -> bool:
        """Process buffered data for complete messages.

        Returns:
            False once a frame could not be read and the connection has been
            given up on; the caller stops reading from it.
        """
        while True:
            async with self._buffer_lock:
                if len(self._buffer) == 0:
                    return True
                buffer_snapshot = bytes(self._buffer)

                status, headers, payload, msg_source, offset = parse_http_headers(
                    buffer_snapshot
                )

                if not status:
                    return True

                try:
                    content_length = int(headers.get(HEADER_CONTENT_LENGTH, "0"))
                except ValueError:
                    _LOGGER.warning(
                        "Invalid Content-Length header: %s",
                        headers.get(HEADER_CONTENT_LENGTH),
                    )
                    content_length = 0
                if len(payload) < content_length:
                    return True

                actual_payload = payload[:content_length]
                message_size = offset + content_length

                self._buffer = bytearray(self._buffer[message_size:])

            try:
                parsed_data = local_ctrl_codec.parse_payload(
                    msg_source, self.security_ctx, actual_payload
                )

                if parsed_data.get(FIELD_STATUS) == -1:
                    error_msg = parsed_data.get(FIELD_ERROR, "Unknown error")

                    _LOGGER.warning(
                        "Message parsing failed (first_msg=%s, source=%s): %s",
                        not self._first_message_processed,
                        msg_source,
                        error_msg,
                    )

                    if msg_source == MessageSource.QUERY_RESPONSE:
                        self._answer_pending_query((msg_source, parsed_data))

                    self._fail_connection()
                    return False

                self._first_message_processed = True

                if not (
                    msg_source == MessageSource.QUERY_RESPONSE
                    and self._answer_pending_query((msg_source, parsed_data))
                ):
                    await self._invoke_callbacks(msg_source, parsed_data)

            except (ValueError, TypeError, AttributeError):
                _LOGGER.exception("Error parsing payload in message listener")
                self._fail_connection()
                return False

    async def _invoke_callbacks(self, msg_source, data):
        """Invoke registered callbacks with message data."""
        for callback in list(self.callbacks):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(msg_source, data)
                else:
                    callback(msg_source, data)
            except (ValueError, TypeError, RuntimeError):
                _LOGGER.exception("Error in message callback")

    def _get_socket(self) -> socket.socket | None:
        """Get underlying socket from transport."""
        if self.transport is None:
            return None
        return self.transport.socket()
