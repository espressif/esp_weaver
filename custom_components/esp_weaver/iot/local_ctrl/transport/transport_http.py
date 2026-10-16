# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""HTTP transport implementation for ESP Local Control.

This module provides HTTP-based transport for communicating with
ESP devices using the local control protocol.
"""
import asyncio
import contextlib
import logging
import socket
import threading
from http.client import HTTPConnection

from ..const import HTTP_CONNECTION_TIMEOUT
from ..utils import str_to_bytes
from .transport import Transport

__all__ = ["TransportHTTP"]

_LOGGER = logging.getLogger(__name__)


class TransportHTTP(Transport):
    """HTTP transport for ESP Local Control protocol."""

    def __init__(self, hostname: str) -> None:
        """Open a connection to the device.

        Plain HTTP: esp_local_ctrl secures the payload itself, at the
        protocomm layer, and advertises no TLS.

        Args:
            hostname: Device hostname or IP address.

        Raises:
            RuntimeError: The device could not be connected to.
        """
        self._lock = threading.Lock()
        self.conn: HTTPConnection | None = HTTPConnection(
            hostname, timeout=HTTP_CONNECTION_TIMEOUT
        )
        try:
            self.conn.connect()
        except OSError as err:
            raise RuntimeError("Connection Failure : " + str(err)) from err
        self.headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
        }

    def _send_post_request(self, path: str, data: str | bytes) -> str:
        """Send POST and wait for response (synchronous)."""
        with self._lock:
            if self.conn is None:
                raise RuntimeError("Connection not available")
            data = str_to_bytes(data) if isinstance(data, str) else data
            try:
                self.conn.request("POST", path, data, self.headers)
                response = self.conn.getresponse()
                for hdr_key, hdr_val in response.getheaders():
                    if hdr_key == "Set-Cookie":
                        self.headers["Cookie"] = hdr_val
                if response.status == 200:
                    return response.read().decode("latin-1")
                # Consume response body to keep connection reusable
                response.read()
            except OSError as err:
                raise RuntimeError("Connection Failure : " + str(err)) from err
            raise RuntimeError(
                "Server responded with error code " + str(response.status)
            )

    def _send_post_request_no_response(self, path: str, data: str | bytes) -> None:
        """Send POST request without waiting for response (for listener mode)."""
        with self._lock:
            data = str_to_bytes(data) if isinstance(data, str) else data
            try:
                if self.conn is not None and self.conn.sock:
                    host = self.conn.host
                    if self.conn.port and self.conn.port != 80:
                        host_header = f"{host}:{self.conn.port}"
                    else:
                        host_header = host

                    request_line = f"POST {path} HTTP/1.1\r\n"
                    headers = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
                    headers += f"Host: {host_header}\r\n"
                    headers += f"Content-Length: {len(data)}\r\n"
                    headers += "\r\n"

                    request = (request_line + headers).encode("latin-1") + data
                    self.conn.sock.sendall(request)
                else:
                    raise RuntimeError("Connection not available")
            except OSError as err:
                raise RuntimeError("Connection Failure : " + str(err)) from err

    def reset_connection(self) -> None:
        """Clear http.client internal state, keep socket open for listener.

        http.client refuses a new request while it believes the previous
        response is unread, and after the handshake the socket is read as a
        stream by the listener instead. The state lives in private attributes
        of HTTPConnection, so this reaches into them; there is no public way.
        """
        with self._lock:
            try:
                if self.conn is None:
                    return

                if hasattr(self.conn, "_HTTPConnection__response"):
                    response_obj = self.conn._HTTPConnection__response
                    if response_obj:
                        with contextlib.suppress(OSError, AttributeError):
                            response_obj.close()
                    self.conn._HTTPConnection__response = None
                if hasattr(self.conn, "_method"):
                    self.conn._method = None
            except (OSError, AttributeError):
                _LOGGER.debug("Unexpected exception in reset_connection", exc_info=True)

    def close(self) -> None:
        """Close HTTP connection gracefully."""
        with self._lock:
            try:
                if self.conn is not None:
                    sock = self.conn.sock
                    if sock:
                        with contextlib.suppress(OSError):
                            sock.shutdown(socket.SHUT_RDWR)
                        with contextlib.suppress(OSError):
                            sock.close()
                        self.conn.sock = None
                    with contextlib.suppress(OSError):
                        self.conn.close()
                    self.conn = None
            except (OSError, AttributeError):
                _LOGGER.debug("Exception during connection close", exc_info=True)

    def socket(self) -> socket.socket | None:
        """Return the connection's socket, if it is open."""
        conn = self.conn
        if conn is None:
            return None
        return conn.sock

    def is_socket_healthy(self) -> bool:
        """Check if the underlying socket is healthy and connection is valid.

        Uses minimal checks to avoid false positives that could cause
        unnecessary disconnections during active data streams.
        Only checks socket descriptor validity and TCP-level errors.
        Does NOT use recv(MSG_PEEK) as it can cause false positives
        in certain race conditions with non-blocking I/O.

        Returns:
            True if socket is healthy, False otherwise.
        """
        with self._lock:
            try:
                if self.conn is None or self.conn.sock is None:
                    return False

                sock = self.conn.sock

                # Check socket file descriptor
                try:
                    fileno = sock.fileno()
                    if fileno == -1:
                        return False
                except OSError:
                    return False

                # Check for errors detected by TCP keepalive
                # This catches RST packets and other TCP-level errors
                try:
                    errcode = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if errcode != 0:
                        return False
                except OSError:
                    return False

                # Note: recv(MSG_PEEK) check removed to prevent false positives
                # Real connection failures will be caught by:
                # 1. SO_ERROR check above (catches RST, connection reset)
                # 2. TCP keepalive (configured in client.py)
                # 3. Actual send/recv failures during data operations

                return True

            except (OSError, AttributeError):
                return False

    async def send_data(self, ep_name: str, data: str | bytes) -> str:
        """Send and return response.

        Args:
            ep_name: Endpoint name.
            data: Data to send.

        Returns:
            Response data from device.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._send_post_request, "/" + ep_name, data
        )

    async def send_data_no_response(self, ep_name: str, data: str | bytes) -> None:
        """Send without waiting for response.

        Args:
            ep_name: Endpoint name.
            data: Data to send.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._send_post_request_no_response, "/" + ep_name, data
        )
