# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the local-control message listener's failure handling."""

import asyncio
from unittest.mock import MagicMock, patch

from cryptography.exceptions import InvalidTag
import pytest

from custom_components.esp_weaver.iot.local_ctrl.local_ctrl import HTTPMessageListener

_FRAME = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/x-www-form-urlencoded\r\n"
    b"Content-Length: 4\r\n"
    b"\r\n"
    b"\xde\xad\xbe\xef"
)


@pytest.fixture
def client() -> MagicMock:
    """Return the client the listener reports connection errors to."""
    client = MagicMock()
    client.node_id = "node"
    return client


@pytest.fixture
def security() -> MagicMock:
    """Return the security context the listener decrypts frames with."""
    return MagicMock()


@pytest.fixture
def listener(client: MagicMock, security: MagicMock) -> HTTPMessageListener:
    """Return a listener with a mocked transport, security context and client."""
    return HTTPMessageListener(
        transport=MagicMock(), security_ctx=security, client=client
    )


class TestBufferProcessing:
    """Turning bytes off the socket into messages."""

    async def test_a_frame_that_will_not_decrypt_fails_the_connection(
        self, listener: HTTPMessageListener, client: MagicMock, security: MagicMock
    ) -> None:
        """An InvalidTag on a frame ends the connection rather than the task."""
        security.decrypt_data.side_effect = InvalidTag()
        listener._buffer.extend(_FRAME)

        keep_reading = await listener._process_buffer()

        assert keep_reading is False
        client.mark_connection_error.assert_called_once()

    async def test_a_partial_frame_waits_for_more(
        self, listener: HTTPMessageListener, client: MagicMock
    ) -> None:
        """Half a frame is not an error; the rest is still on its way."""
        listener._buffer.extend(_FRAME[:-2])

        keep_reading = await listener._process_buffer()

        assert keep_reading is True
        client.mark_connection_error.assert_not_called()
        assert bytes(listener._buffer) == _FRAME[:-2]

    async def test_a_parser_exception_fails_the_connection(
        self, listener: HTTPMessageListener, client: MagicMock
    ) -> None:
        """An error the codec did not absorb still ends up at the client."""
        listener._buffer.extend(_FRAME)

        with patch(
            "custom_components.esp_weaver.iot.local_ctrl.local_ctrl."
            "local_ctrl_codec.parse_payload",
            side_effect=TypeError("boom"),
        ):
            keep_reading = await listener._process_buffer()

        assert keep_reading is False
        client.mark_connection_error.assert_called_once()


class TestListenLoop:
    """The task that owns the socket."""

    @pytest.mark.parametrize(
        "error",
        [RuntimeError("unexpected"), KeyError("missing")],
        ids=["runtime", "key"],
    )
    async def test_an_unexpected_error_marks_the_connection_and_stops(
        self, listener: HTTPMessageListener, client: MagicMock, error: Exception
    ) -> None:
        """Whatever kills the loop, ``_running`` is False and the client knows."""
        listener._running = True

        with patch.object(listener, "_listen", side_effect=error):
            await listener._listen_loop()

        assert listener._running is False
        client.mark_connection_error.assert_called_once()

    async def test_cancellation_is_not_a_connection_error(
        self, listener: HTTPMessageListener, client: MagicMock
    ) -> None:
        """Being stopped on purpose is not the device going away."""
        listener._running = True

        with (
            patch.object(listener, "_listen", side_effect=asyncio.CancelledError()),
            pytest.raises(asyncio.CancelledError),
        ):
            await listener._listen_loop()

        assert listener._running is False
        client.mark_connection_error.assert_not_called()

    async def test_a_bad_frame_ends_the_loop(
        self, listener: HTTPMessageListener, client: MagicMock, security: MagicMock
    ) -> None:
        """The loop stops reading once a frame has failed the connection."""
        sock = MagicMock()
        sock.gettimeout.return_value = 1.0
        sock.recv.return_value = _FRAME
        security.decrypt_data.side_effect = InvalidTag()

        with patch.object(listener, "_get_socket", return_value=sock):
            assert await listener.start()
            assert listener._listen_task is not None
            await asyncio.wait_for(listener._listen_task, timeout=2)

        assert listener._running is False
        client.mark_connection_error.assert_called_once()
        # Exactly one read: the loop did not spin on a connection it had
        # already given up on.
        assert sock.recv.call_count == 1

    async def test_a_dead_listener_can_be_started_again(
        self, listener: HTTPMessageListener
    ) -> None:
        """After the loop has died, start() is not refused as already running."""
        listener._running = True

        with patch.object(listener, "_listen", side_effect=RuntimeError("x")):
            await listener._listen_loop()

        with patch.object(listener, "_listen", return_value=None):
            assert await listener.start()
            assert listener._listen_task is not None
            await listener._listen_task
