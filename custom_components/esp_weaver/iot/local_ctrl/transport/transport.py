# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""The interface every protocomm transport offers.

The client and the message listener talk to a transport through this and
nothing else, so a transport that is not HTTP needs only to implement it - no
caller reaches for an attribute the base class does not name.
"""

from abc import ABC, abstractmethod
import socket


class Transport(ABC):
    """Carries protocomm payloads to one endpoint of one device."""

    @abstractmethod
    async def send_data(self, ep_name: str, data: str | bytes) -> str:
        """Send a payload to an endpoint and return the device's answer.

        Raises:
            RuntimeError: The request could not be sent or was answered with
                an error status.
        """

    @abstractmethod
    async def send_data_no_response(self, ep_name: str, data: str | bytes) -> None:
        """Send a payload without waiting for an answer.

        The answer arrives on the same connection later and is picked up by
        whoever is reading its socket; see :meth:`socket`.

        Raises:
            RuntimeError: The request could not be sent.
        """

    @abstractmethod
    def close(self) -> None:
        """Release the connection. Safe to call more than once."""

    def reset_connection(self) -> None:
        """Forget any request-response state, keeping the connection open.

        Called once the session handshake is over and the connection is about
        to be read as a stream. Transports that keep no such state need not
        override it.
        """

    def is_socket_healthy(self) -> bool:
        """Say whether the connection is still believed usable.

        Transports that cannot tell answer True: a failure then shows up on the
        next send instead.
        """
        return True

    def socket(self) -> socket.socket | None:
        """Return the connection's socket, for reading unsolicited frames.

        None when the transport has no socket to hand out, in which case
        nothing can listen for the device's reports on it.
        """
        return None
