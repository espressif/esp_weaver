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
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..specs.device_specs import (
    HTTP_HEADER_MIN_LENGTH,
    LISTENER_ERROR_SLEEP,
    LISTENER_RECV_TIMEOUT,
    QUERY_TIMEOUT,
    SOCKET_RECV_BUFFER_SIZE,
)
from ..specs.keys import (
    KEY_CONTENT_LENGTH,
    KEY_ERROR,
    KEY_FLAGS,
    KEY_STATUS,
    KEY_TYPE,
    KEY_VALUE,
)

# Use local imports from the bundled library
from . import local_ctrl_codec
from . import security

if TYPE_CHECKING:
    from .security.security import Security
    from .transport.transport import Transport

_LOGGER = logging.getLogger(__name__)

# Set this to true to allow exceptions to be thrown
CONFIG_THROW_EXCEPT = False


# Property types enum
PROP_TYPE_TIMESTAMP = 0
PROP_TYPE_INT32 = 1
PROP_TYPE_BOOLEAN = 2
PROP_TYPE_STRING = 3


# Property flags enum
PROP_FLAG_READONLY = 1 << 0


def prop_typestr(prop):
    """Get property type as string."""
    if prop[KEY_TYPE] == PROP_TYPE_TIMESTAMP:
        return "TIME(us)"
    if prop[KEY_TYPE] == PROP_TYPE_INT32:
        return "INT32"
    if prop[KEY_TYPE] == PROP_TYPE_BOOLEAN:
        return "BOOLEAN"
    if prop[KEY_TYPE] == PROP_TYPE_STRING:
        return "STRING"
    return "UNKNOWN"


def encode_prop_value(prop, value):
    """Encode property value to bytes based on type."""
    try:
        if prop[KEY_TYPE] == PROP_TYPE_TIMESTAMP:
            return struct.pack("q", value)
        if prop[KEY_TYPE] == PROP_TYPE_INT32:
            return struct.pack("i", value)
        if prop[KEY_TYPE] == PROP_TYPE_BOOLEAN:
            return struct.pack("?", value)
        if prop[KEY_TYPE] == PROP_TYPE_STRING:
            return bytes(value, encoding="latin-1")
        return value
    except struct.error as e:
        _LOGGER.warning("Failed to encode property value: %s", e)
    return None


def decode_prop_value(prop, value):
    """Decode property value from bytes based on type."""
    try:
        if prop[KEY_TYPE] == PROP_TYPE_TIMESTAMP:
            return struct.unpack("q", value)[0]
        if prop[KEY_TYPE] == PROP_TYPE_INT32:
            return struct.unpack("i", value)[0]
        if prop[KEY_TYPE] == PROP_TYPE_BOOLEAN:
            return struct.unpack("?", value)[0]
        if prop[KEY_TYPE] == PROP_TYPE_STRING:
            return value.decode("latin-1")
        return value
    except struct.error as e:
        _LOGGER.warning("Failed to decode property value: %s", e)
    return value


def str_to_prop_value(prop, strval):
    """Convert string to property value based on type."""
    try:
        if prop[KEY_TYPE] in (PROP_TYPE_TIMESTAMP, PROP_TYPE_INT32):
            return int(strval)
        if prop[KEY_TYPE] == PROP_TYPE_BOOLEAN:
            return bool(strval)
        if prop[KEY_TYPE] == PROP_TYPE_STRING:
            return strval
        return strval
    except ValueError as e:
        _LOGGER.warning("Failed to convert string to property value: %s", e)
        return None


def prop_is_readonly(prop):
    """Check if property is read-only."""
    return (prop[KEY_FLAGS] & PROP_FLAG_READONLY) != 0


def on_except(err):
    """Handle exception based on CONFIG_THROW_EXCEPT setting."""
    if CONFIG_THROW_EXCEPT:
        raise RuntimeError(err)
    _LOGGER.error(err)


def get_security(
    secver: int,
    sec_patch_ver: int,
    username: str,
    password: str,
    *,
    pop: str = "",
):
    """Get security context for the specified security version.

    Args:
        secver: Security version (0, 1, or 2).
        sec_patch_ver: Security patch version.
        username: Username for security2.
        password: Password for security2.
        pop: Proof of possession for security1.

    Returns:
        Security context instance or None if unsupported.
    """
    if secver == 2:
        return security.Security2(sec_patch_ver, username, password, verbose=False)
    if secver == 1:
        return security.Security1(pop, verbose=False)
    if secver == 0:
        return security.Security0(verbose=False)
    return None


async def get_transport(sel_transport: str, service_name: str):
    """Create transport for the specified protocol."""
    try:
        tp = None
        if sel_transport == "http":
            # Local import to avoid circular dependency
            from .transport import TransportHTTP

            loop = asyncio.get_running_loop()
            tp = await loop.run_in_executor(
                None, TransportHTTP, service_name, None
            )
        else:
            _LOGGER.warning("Unsupported transport type: %s", sel_transport)
        return tp
    except RuntimeError as e:
        on_except(e)
        return None


async def get_sec_patch_ver(tp):
    """Get security patch version from device."""
    try:
        response = await tp.send_data("esp_local_ctrl/version", "---")

        try:
            info = json.loads(response)
            try:
                sec_patch_ver = info["local_ctrl"]["sec_patch_ver"]
            except KeyError:
                sec_patch_ver = 0
            return sec_patch_ver

        except ValueError:
            return 0

    except RuntimeError as e:
        on_except(e)
        return None


async def version_match(tp, protover):
    """Check if protocol version matches."""
    try:
        response = await tp.send_data("esp_local_ctrl/version", protover)

        if response.lower() == protover.lower():
            return True

        try:
            info = json.loads(response)
            if info["local_ctrl"]["ver"].lower() == protover.lower():
                return True

        except ValueError:
            return False

    except RuntimeError as e:
        on_except(e)
        return None
    return False


async def has_capability(tp, capability: str = "none"):
    """Check if device has a specific capability."""
    try:
        response = await tp.send_data("esp_local_ctrl/version", capability)

        try:
            info = json.loads(response)
            try:
                supported_capabilities = info["local_ctrl"]["cap"]
                if capability.lower() == "none" or capability in supported_capabilities:
                    return True
                return False
            except KeyError:
                return False

        except ValueError:
            return False

    except RuntimeError as e:
        on_except(e)

    return False


MAX_HANDSHAKE_ATTEMPTS = 10


async def establish_session(tp: "Transport", sec: "Security") -> bool:
    """Establish security session with device."""
    try:
        response = None
        attempts = 0
        while True:
            attempts += 1
            if attempts > MAX_HANDSHAKE_ATTEMPTS:
                on_except(
                    RuntimeError(
                        f"Session handshake exceeded {MAX_HANDSHAKE_ATTEMPTS} attempts"
                    )
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
        on_except(e)
        return False


async def get_all_property_values(tp, security_ctx):
    """Get all property values from device."""
    try:
        props = []
        message = local_ctrl_codec.get_prop_count_request(security_ctx)
        response = await tp.send_data("esp_local_ctrl/control", message)
        count = local_ctrl_codec.get_prop_count_response(security_ctx, response)
        if count == 0:
            raise RuntimeError("No properties found!")
        indices = list(range(count))
        message = local_ctrl_codec.get_prop_vals_request(security_ctx, indices)
        response = await tp.send_data("esp_local_ctrl/control", message)
        props = local_ctrl_codec.get_prop_vals_response(security_ctx, response)
        if len(props) != count:
            raise RuntimeError(
                f"Incorrect count of properties: got {len(props)}, expected {count}"
            )
        for p in props:
            p[KEY_VALUE] = decode_prop_value(p, p[KEY_VALUE])
        return props
    except RuntimeError as e:
        on_except(e)
        return []


async def set_property_values(
    tp,
    security_ctx,
    props,
    indices,
    values,
    *,
    check_readonly: bool = False,
    listener=None,
):
    """Set property values on device.

    Args:
        tp: Transport instance.
        security_ctx: Security context for encryption.
        props: Property definitions (only needed if check_readonly=True).
        indices: List of property indices to set.
        values: List of values to set.
        check_readonly: Whether to check if properties are read-only.
        listener: HTTPMessageListener for async message handling (required).

    Returns:
        True if successful, False otherwise.
    """
    try:
        if check_readonly:
            for index in indices:
                if index < 0 or index >= len(props):
                    raise RuntimeError(f"Property index {index} out of range")
                if prop_is_readonly(props[index]):
                    raise RuntimeError("Cannot set value of Read-Only property")

        message = local_ctrl_codec.set_prop_vals_request(security_ctx, indices, values)

        if not listener:
            _LOGGER.warning("set_property_values called without listener")
            return False

        msg_source, parsed_data = await listener.send_query_and_wait(
            tp, message, timeout=QUERY_TIMEOUT
        )
        if msg_source is None:
            return False
        if isinstance(parsed_data, dict):
            return parsed_data.get(KEY_STATUS, 0) == 0
        return local_ctrl_codec.set_prop_vals_response(security_ctx, parsed_data)
    except RuntimeError as e:
        on_except(e)
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


class HTTPMessageListener:
    """Listener for HTTP messages from ESP device."""

    def __init__(
        self,
        transport: "Transport",
        security_ctx: "Security",
        client: Any = None,
        verbose: bool = False,
    ) -> None:
        """Initialize HTTP message listener.

        Args:
            transport: Transport instance.
            security_ctx: Security context for decryption.
            client: Client instance for error callbacks.
            verbose: Enable verbose logging.
        """
        self.transport = transport
        self.security_ctx = security_ctx
        self.client = client
        self.verbose = verbose
        self.callbacks: list[Callable[..., Any]] = []
        self._running = False
        self._listen_task: asyncio.Task[None] | None = None
        self._buffer = bytearray()
        self._buffer_lock = asyncio.Lock()
        self._query_futures: dict[str, asyncio.Future[Any]] = {}
        self._query_counter = 0
        self._query_lock = asyncio.Lock()
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
            self._query_futures[query_id] = response_future

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
            except asyncio.CancelledError:
                return (None, None)
            finally:
                self._query_futures.pop(query_id, None)

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
        self._query_futures.clear()

    async def _listen_loop(self):
        """Main listening loop."""
        while self._running:
            try:
                sock = self._get_socket()
                if not sock:
                    _LOGGER.warning(
                        "HTTPMessageListener: socket is None, exiting listen loop, "
                        "node_id=%s",
                        self.client.node_id if self.client else "unknown",
                    )
                    break

                try:
                    loop = asyncio.get_running_loop()
                    data = await asyncio.wait_for(
                        loop.run_in_executor(None, sock.recv, SOCKET_RECV_BUFFER_SIZE),
                        timeout=LISTENER_RECV_TIMEOUT,
                    )
                except TimeoutError:
                    continue
                except (ConnectionResetError, BrokenPipeError, OSError) as err:
                    _LOGGER.warning(
                        "HTTPMessageListener: connection error in recv for node %s: %s",
                        self.client.node_id if self.client else "unknown",
                        err,
                    )
                    if self.client and hasattr(self.client, "mark_connection_error"):
                        self.client.mark_connection_error()
                    break

                if not data:
                    _LOGGER.warning(
                        "HTTPMessageListener: connection closed by peer, node_id=%s",
                        self.client.node_id if self.client else "unknown",
                    )
                    if self.client and hasattr(self.client, "mark_connection_error"):
                        self.client.mark_connection_error()
                    break

                async with self._buffer_lock:
                    self._buffer.extend(data)

                await self._process_buffer()

            except asyncio.CancelledError:
                break
            except (ConnectionError, OSError) as err:
                _LOGGER.debug(
                    "HTTPMessageListener: connection error in listen loop: %s", err
                )
                await asyncio.sleep(LISTENER_ERROR_SLEEP)

    async def _process_buffer(self):
        """Process buffered data for complete messages."""
        while True:
            async with self._buffer_lock:
                if len(self._buffer) == 0:
                    break
                buffer_snapshot = bytes(self._buffer)

                status, headers, payload, msg_source, offset = parse_http_headers(
                    buffer_snapshot
                )

                if not status:
                    break

                try:
                    content_length = int(headers.get(KEY_CONTENT_LENGTH, "0"))
                except ValueError:
                    _LOGGER.warning(
                        "Invalid Content-Length header: %s",
                        headers.get(KEY_CONTENT_LENGTH),
                    )
                    content_length = 0
                if len(payload) < content_length:
                    break

                actual_payload = payload[:content_length]
                message_size = offset + content_length

                self._buffer = bytearray(self._buffer[message_size:])

            try:
                parsed_data = local_ctrl_codec.parse_payload(
                    msg_source, self.security_ctx, actual_payload
                )

                if parsed_data.get(KEY_STATUS) == -1:
                    error_msg = parsed_data.get(KEY_ERROR, "Unknown error")

                    _LOGGER.warning(
                        "Message parsing failed (first_msg=%s, source=%s): %s",
                        not self._first_message_processed,
                        msg_source,
                        error_msg,
                    )

                    if (
                        msg_source == MessageSource.QUERY_RESPONSE
                        and self._query_futures
                    ):
                        query_id = next(iter(self._query_futures))
                        future = self._query_futures.pop(query_id, None)
                        if future and not future.done():
                            future.set_result((msg_source, parsed_data))

                    if self.client and hasattr(self.client, "mark_connection_error"):
                        self.client.mark_connection_error()
                    break

                self._first_message_processed = True

                if msg_source == MessageSource.QUERY_RESPONSE and self._query_futures:
                    query_id = next(iter(self._query_futures))
                    future = self._query_futures.pop(query_id, None)
                    if future and not future.done():
                        future.set_result((msg_source, parsed_data))
                else:
                    await self._invoke_callbacks(msg_source, parsed_data)

            except (ValueError, TypeError, AttributeError):
                _LOGGER.exception("Error parsing payload in message listener")
                break

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

    def _get_socket(self):
        """Get underlying socket from transport."""
        try:
            if self.transport and hasattr(self.transport, "conn"):
                conn = self.transport.conn
                if conn and hasattr(conn, "sock"):
                    return conn.sock
        except (AttributeError, RuntimeError) as err:
            _LOGGER.debug("HTTPMessageListener: failed to get socket: %s", err)
        return None
