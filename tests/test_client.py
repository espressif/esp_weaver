# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver client module."""

import asyncio
from collections.abc import Coroutine
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.client.client import (
    ESPLocalCtrlClient,
    MessageSource,
)
from custom_components.esp_weaver.iot.client.client_utils import (
    convert_values_to_esp_format,
    parse_property_count_response,
    parse_property_values_response,
)


@pytest.fixture
def client() -> ESPLocalCtrlClient:
    """Create a test client with default parameters."""
    return ESPLocalCtrlClient(
        node_id="test_node",
        ip="192.168.1.100",
    )


class TestESPLocalCtrlClientInit:
    """Test ESPLocalCtrlClient initialization."""

    def test_basic_initialization(self, client: ESPLocalCtrlClient) -> None:
        """Test basic client initialization."""
        assert client.node_id == "test_node"
        assert client.ip == "192.168.1.100"
        assert client.port == 8080  # Default port
        assert client.pop is None
        assert client.security_mode == 1  # Default security mode
        assert client.transport is None
        assert client.session_established is False

    def test_initialization_with_all_params(self) -> None:
        """Test client initialization with all parameters."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
            port=9090,
            pop="secret_pop",
            security_mode=0,
        )

        assert client.port == 9090
        assert client.pop == "secret_pop"
        assert client.security_mode == 0

    def test_initialization_creates_lock(self) -> None:
        """Test that initialization creates control lock."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        assert hasattr(client, "_control_lock")
        assert isinstance(client._control_lock, asyncio.Lock)


class TestESPLocalCtrlClientState:
    """Test ESPLocalCtrlClient state management."""

    def test_initial_state(self) -> None:
        """Test client initial state."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        assert client._connection_error is False

    def test_update_ip(self) -> None:
        """Test updating client IP."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        client.ip = "192.168.1.200"
        assert client.ip == "192.168.1.200"

    def test_update_port(self) -> None:
        """Test updating client port."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        client.port = 9000
        assert client.port == 9000


class TestESPLocalCtrlClientConnection:
    """Test ESPLocalCtrlClient connection methods."""

    async def test_is_connected_no_transport(self) -> None:
        """Test is_connected returns False when no transport."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = None

        result = await client.is_connected()
        assert result is False

    async def test_is_connected_no_session(self) -> None:
        """Test is_connected returns False when no session."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = False

        result = await client.is_connected()
        assert result is False

    async def test_is_connected_with_connection_error(self) -> None:
        """Test is_connected returns False when connection error."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client._connection_error = True

        result = await client.is_connected()
        assert result is False


class TestESPLocalCtrlClientDisconnect:
    """Test ESPLocalCtrlClient disconnect methods."""

    async def test_disconnect_no_transport(self) -> None:
        """Test disconnect when no transport exists."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = None

        # Should not raise
        await client.disconnect()
        assert client.session_established is False

    async def test_disconnect_clears_state(self) -> None:
        """Test disconnect clears session state."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.session_established = True
        client.transport = MagicMock()
        client.transport.close = MagicMock()

        # Save transport mock reference before disconnect sets it to None
        transport_mock = client.transport

        await client.disconnect()

        assert client.session_established is False
        transport_mock.close.assert_called_once()


class TestESPLocalCtrlClientErrorHandler:
    """Test ESPLocalCtrlClient error handling."""

    def test_set_connection_error_callback(self, client: ESPLocalCtrlClient) -> None:
        """Test setting connection error callback."""
        callback = MagicMock()
        client.set_connection_error_callback(callback)

        # Callback should be stored
        assert client._connection_error_callback == callback

    def test_connection_error_flag(self, client: ESPLocalCtrlClient) -> None:
        """Test connection error flag management."""
        # Initially no error
        assert client._connection_error is False

        # Set error
        client._connection_error = True
        assert client._connection_error is True


class TestClientUtilsFunctions:
    """Test client utility functions."""

    def test_convert_values_to_esp_format_empty(self) -> None:
        """Test converting empty values."""
        result = convert_values_to_esp_format([])
        assert result == []

    def test_convert_values_to_esp_format_basic(self) -> None:
        """Test converting basic values."""
        values = [{"power": True}, 128, "hello"]
        result = convert_values_to_esp_format(values)
        assert result == [b'{"power": true}', b"128", b"hello"]

    def test_parse_property_count_response(self) -> None:
        """Test parsing property count response."""
        # Test with valid response
        response = {"count": 5}
        result = parse_property_count_response(response)
        assert result == 5

    def test_parse_property_values_response_empty(self) -> None:
        """Test parsing empty property values response."""
        result = parse_property_values_response(None)
        assert result == []


class TestESPLocalCtrlClientConnect:
    """Test ESPLocalCtrlClient connect method."""

    async def test_is_connected_returns_true_when_established(self) -> None:
        """Test is_connected returns True when session established."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.is_socket_healthy = MagicMock(return_value=True)
        client.session_established = True
        client._connection_error = False

        result = await client.is_connected()
        assert result is True

    async def test_connect_creates_transport(self) -> None:
        """Test connect creates transport and security context."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
            port=8080,
            pop="test_pop",
        )

        with (
            patch.object(client, "_cleanup_session", new_callable=AsyncMock),
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl"
            ) as mock_esp,
        ):
            mock_transport = MagicMock()
            mock_esp.get_transport = AsyncMock(return_value=mock_transport)
            mock_esp.get_security = MagicMock(return_value=MagicMock())
            mock_esp.establish_session = AsyncMock(return_value=True)

            with (
                patch.object(client, "_enable_tcp_keepalive", new_callable=AsyncMock),
                patch.object(client, "_start_message_listener", new_callable=AsyncMock),
            ):
                result = await client.connect()

                assert result is True
                assert client.session_established is True
                mock_esp.get_transport.assert_called_once()

    async def test_connect_transport_failure(self) -> None:
        """Test connect fails when transport creation fails."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.get_transport = AsyncMock(return_value=None)

            result = await client.connect()
            assert result is False

    async def test_connect_security_context_failure(self) -> None:
        """Test connect fails when security context creation fails."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.get_transport = AsyncMock(return_value=MagicMock())
            mock_esp.get_security = MagicMock(return_value=None)

            result = await client.connect()
            assert result is False

    async def test_connect_session_establishment_failure(self) -> None:
        """Test connect fails when session establishment fails."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.get_transport = AsyncMock(return_value=MagicMock())
            mock_esp.get_security = MagicMock(return_value=MagicMock())
            mock_esp.establish_session = AsyncMock(return_value=False)

            with patch.object(client, "disconnect", new_callable=AsyncMock):
                result = await client.connect()
                assert result is False

    async def test_connect_handles_oserror(self) -> None:
        """Test connect handles OSError gracefully."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.get_transport = AsyncMock(side_effect=OSError("Network error"))

            with patch.object(client, "disconnect", new_callable=AsyncMock):
                result = await client.connect()
                assert result is False

    async def test_connect_handles_timeout_error(self) -> None:
        """Test connect handles TimeoutError gracefully."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.get_transport = AsyncMock(side_effect=TimeoutError())

            with patch.object(client, "disconnect", new_callable=AsyncMock):
                result = await client.connect()
                assert result is False


class TestESPLocalCtrlClientEnsureConnected:
    """Test ESPLocalCtrlClient _ensure_connected method."""

    async def test_ensure_connected_with_error_flag(self) -> None:
        """Test _ensure_connected reconnects when error flag is set."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = True

        with patch.object(
            client, "connect", new_callable=AsyncMock, return_value=True
        ) as mock_connect:
            result = await client._ensure_connected()

            # connect() handles cleanup internally under _connect_lock
            mock_connect.assert_called_once()
            assert result is True

    async def test_ensure_connected_no_transport(self) -> None:
        """Test _ensure_connected connects when no transport."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = None
        client._connection_error = False

        with patch.object(
            client, "connect", new_callable=AsyncMock, return_value=True
        ) as mock_connect:
            result = await client._ensure_connected()
            mock_connect.assert_called_once()
            assert result is True

    async def test_ensure_connected_already_connected(self) -> None:
        """Test _ensure_connected returns True when already connected."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client._connection_error = False

        result = await client._ensure_connected()
        assert result is True


class TestESPLocalCtrlClientGetPropertyValues:
    """Test ESPLocalCtrlClient get_property_values method."""

    async def test_get_property_values_not_connected(self) -> None:
        """Test get_property_values returns empty list when not connected."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch.object(
            client, "_ensure_connected", new_callable=AsyncMock, return_value=False
        ):
            result = await client.get_property_values()
            assert result == []

    async def test_get_property_values_success(self) -> None:
        """Test get_property_values returns properties."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        mock_properties = [{"name": "config", "value": b"{}"}]

        with (
            patch.object(
                client, "_ensure_connected", new_callable=AsyncMock, return_value=True
            ),
            patch.object(
                client,
                "_get_all_property_values_via_listener",
                new_callable=AsyncMock,
                return_value=mock_properties,
            ),
        ):
            result = await client.get_property_values()
            assert result == mock_properties


class TestESPLocalCtrlClientSetPropertyValues:
    """Test ESPLocalCtrlClient set_property_values method."""

    async def test_set_property_values_connection_error(self) -> None:
        """Test set_property_values returns False on connection error."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = True

        result = await client.set_property_values([1], [{"power": True}])
        assert result is False

    async def test_set_property_values_no_session(self) -> None:
        """Test set_property_values returns False when no session."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = None
        client.session_established = False

        result = await client.set_property_values([1], [{"power": True}])
        assert result is False

    async def test_set_property_values_success(self) -> None:
        """Test set_property_values success."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._http_listener = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.set_property_values = AsyncMock(return_value=True)

            result = await client.set_property_values([1], [{"power": True}])
            assert result is True

    async def test_set_property_values_timeout(self) -> None:
        """Test set_property_values handles timeout."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._http_listener = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            # Simulate timeout - use AsyncMock that raises TimeoutError
            mock_esp.set_property_values = AsyncMock(return_value=True)

            # Patch asyncio.wait_for to raise TimeoutError while still awaiting the coroutine
            async def mock_wait_for(
                coro: Coroutine[Any, Any, Any], timeout: float | None = None
            ) -> None:
                """Mock wait_for that awaits the coroutine then raises TimeoutError."""
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    # Actually await the coroutine to prevent warning
                    await coro
                raise TimeoutError

            with patch(
                "custom_components.esp_weaver.iot.client.client.asyncio.wait_for",
                side_effect=mock_wait_for,
            ):
                result = await client.set_property_values([1], [{"power": True}])
                assert result is False
                assert client._connection_error is True

    async def test_set_property_values_config_redirect(self) -> None:
        """Test set_property_values redirects config(0) to params(1)."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._http_listener = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.set_property_values = AsyncMock(return_value=True)

            result = await client.set_property_values([0], [{"power": True}])
            assert result is True

            # Verify the index was redirected from 0 to 1
            call_args = mock_esp.set_property_values.call_args
            assert call_args is not None
            # indices is passed as keyword argument
            called_indices = call_args.kwargs.get("indices")
            assert called_indices == [1], (
                "Config index 0 should be redirected to params index 1"
            )


class TestESPLocalCtrlClientConnectAlreadyConnected:
    """Test ESPLocalCtrlClient connect when already connected (lines 104-107)."""

    async def test_connect_already_connected(self) -> None:
        """Test connect returns True when already connected."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.is_socket_healthy = MagicMock(return_value=True)
        client.session_established = True
        client._connection_error = False

        result = await client.connect()

        assert result is True


class TestESPLocalCtrlClientGetPropertyValuesWarning:
    """Test get_property_values warning log (line 296)."""

    async def test_get_property_values_empty_props_warning(self) -> None:
        """Test get_property_values logs warning when no properties returned."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with (
            patch.object(
                client, "_ensure_connected", new_callable=AsyncMock, return_value=True
            ),
            patch.object(
                client,
                "_get_all_property_values_via_listener",
                new_callable=AsyncMock,
                return_value=[],  # Empty list triggers warning
            ),
        ):
            result = await client.get_property_values()
            assert result == []


class TestESPLocalCtrlClientGetPropertyValuesException:
    """Test get_property_values exception handling (lines 300-305)."""

    async def test_get_property_values_oserror(self) -> None:
        """Test get_property_values handles OSError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with (
            patch.object(
                client, "_ensure_connected", new_callable=AsyncMock, return_value=True
            ),
            patch.object(
                client,
                "_get_all_property_values_via_listener",
                new_callable=AsyncMock,
                side_effect=OSError("Network error"),
            ),
        ):
            result = await client.get_property_values()
            assert result == []

    async def test_get_property_values_connection_error(self) -> None:
        """Test get_property_values handles ConnectionError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with (
            patch.object(
                client, "_ensure_connected", new_callable=AsyncMock, return_value=True
            ),
            patch.object(
                client,
                "_get_all_property_values_via_listener",
                new_callable=AsyncMock,
                side_effect=ConnectionError("Connection lost"),
            ),
        ):
            result = await client.get_property_values()
            assert result == []


class TestESPLocalCtrlClientGetAllPropsWrongSource:
    """Test _get_all_property_values_via_listener wrong message source (line 360)."""

    async def test_get_all_props_wrong_source_on_values_query(self) -> None:
        """Test returns empty when values query returns wrong message source."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()

        mock_listener = MagicMock()
        client._http_listener = mock_listener

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl_codec"
        ) as mock_proto:
            mock_proto.get_prop_count_request = MagicMock(return_value=b"count_req")
            mock_proto.get_prop_vals_request = MagicMock(return_value=b"vals_req")

            with patch(
                "custom_components.esp_weaver.iot.client.client.MessageSource"
            ) as mock_source:
                mock_source.QUERY_RESPONSE = "expected"

                # First call (count) succeeds, second call (values) has wrong source
                mock_listener.send_query_and_wait = AsyncMock(
                    side_effect=[
                        ("expected", {"count": 2}),  # Count query succeeds
                        (
                            "wrong_source",
                            {"properties": []},
                        ),  # Values query wrong source
                    ]
                )

                result = await client._get_all_property_values_via_listener()
                assert result == []


class TestESPLocalCtrlClientCleanupSessionConnClose:
    """Test _cleanup_session with conn.close() path (lines 468-469)."""

    async def test_cleanup_session_uses_conn_close(self) -> None:
        """Test _cleanup_session uses conn.close() when transport lacks close method."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_conn = MagicMock()
        mock_conn.close = MagicMock()
        # Transport without close method but with conn
        mock_transport = MagicMock(spec=["conn"])
        mock_transport.conn = mock_conn
        client.transport = mock_transport
        client.session_established = True

        with patch.object(client, "_stop_message_listener", new_callable=AsyncMock):
            await client._cleanup_session()

        mock_conn.close.assert_called_once()
        assert client.transport is None


class TestESPLocalCtrlClientTcpKeepaliveNoSock:
    """Test _enable_tcp_keepalive when conn.sock is None (line 488)."""

    async def test_enable_tcp_keepalive_sock_is_none(self) -> None:
        """Test _enable_tcp_keepalive does nothing when sock is None."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.conn = MagicMock()
        client.transport.conn.sock = None

        # Should not raise
        await client._enable_tcp_keepalive()


class TestESPLocalCtrlClientStartListenerExceptions:
    """Test _start_message_listener exception handling (lines 529-537)."""

    async def test_start_message_listener_cancelled_error(self) -> None:
        """Test _start_message_listener handles CancelledError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.security_ctx = MagicMock()
        client._http_listener = None

        with patch(
            "custom_components.esp_weaver.iot.client.client.HTTPMessageListener"
        ) as mock_listener_class:
            mock_listener = MagicMock()
            mock_listener.start = AsyncMock(side_effect=asyncio.CancelledError())
            mock_listener_class.return_value = mock_listener

            with pytest.raises(asyncio.CancelledError):
                await client._start_message_listener()

            assert client._http_listener is None

    async def test_start_message_listener_oserror(self) -> None:
        """Test _start_message_listener handles OSError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.security_ctx = MagicMock()
        client._http_listener = None

        with patch(
            "custom_components.esp_weaver.iot.client.client.HTTPMessageListener"
        ) as mock_listener_class:
            mock_listener = MagicMock()
            mock_listener.start = AsyncMock(side_effect=OSError("Network error"))
            mock_listener_class.return_value = mock_listener

            await client._start_message_listener()

            assert client._http_listener is None

    async def test_start_message_listener_runtime_error(self) -> None:
        """Test _start_message_listener handles RuntimeError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.security_ctx = MagicMock()
        client._http_listener = None

        with patch(
            "custom_components.esp_weaver.iot.client.client.HTTPMessageListener"
        ) as mock_listener_class:
            mock_listener = MagicMock()
            mock_listener.start = AsyncMock(side_effect=RuntimeError("Init error"))
            mock_listener_class.return_value = mock_listener

            await client._start_message_listener()

            assert client._http_listener is None


class TestESPLocalCtrlClientStopListenerExceptions:
    """Test _stop_message_listener exception handling (lines 545-552)."""

    async def test_stop_message_listener_cancelled_error(self) -> None:
        """Test _stop_message_listener handles CancelledError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_listener = MagicMock()
        mock_listener.stop = AsyncMock(side_effect=asyncio.CancelledError())
        client._http_listener = mock_listener

        with pytest.raises(asyncio.CancelledError):
            await client._stop_message_listener()

        assert client._http_listener is None

    async def test_stop_message_listener_oserror(self) -> None:
        """Test _stop_message_listener handles OSError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_listener = MagicMock()
        mock_listener.stop = AsyncMock(side_effect=OSError("Network error"))
        client._http_listener = mock_listener

        await client._stop_message_listener()

        assert client._http_listener is None

    async def test_stop_message_listener_runtime_error(self) -> None:
        """Test _stop_message_listener handles RuntimeError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_listener = MagicMock()
        mock_listener.stop = AsyncMock(side_effect=RuntimeError("Invalid state"))
        client._http_listener = mock_listener

        await client._stop_message_listener()

        assert client._http_listener is None


class TestESPLocalCtrlClientMessageCallbacks:
    """Test ESPLocalCtrlClient message callback methods."""

    def test_add_message_callback(self, client: ESPLocalCtrlClient) -> None:
        """Test adding message callback."""
        callback = MagicMock()
        client.add_message_callback(callback)

        assert callback in client._message_callbacks

    def test_add_message_callback_no_duplicate(
        self, client: ESPLocalCtrlClient
    ) -> None:
        """Test adding same callback twice doesn't duplicate."""
        callback = MagicMock()
        client.add_message_callback(callback)
        client.add_message_callback(callback)

        assert client._message_callbacks.count(callback) == 1

    def test_add_message_callback_with_listener(
        self, client: ESPLocalCtrlClient
    ) -> None:
        """Test adding callback when listener already exists."""
        client._http_listener = MagicMock()
        client._http_listener.add_callback = MagicMock()

        callback = MagicMock()
        client.add_message_callback(callback)

        client._http_listener.add_callback.assert_called_once_with(callback)


class TestESPLocalCtrlClientMarkConnectionError:
    """Test ESPLocalCtrlClient mark_connection_error method."""

    def test_mark_connection_error_sets_flag(self) -> None:
        """Test mark_connection_error sets error flag."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False

        client.mark_connection_error()

        assert client._connection_error is True

    def test_mark_connection_error_calls_callback(self) -> None:
        """Test mark_connection_error calls callback."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        callback = MagicMock()
        client._connection_error_callback = callback
        client._connection_error = False

        client.mark_connection_error()

        callback.assert_called_once_with("test_node")

    def test_mark_connection_error_already_set(self) -> None:
        """Test mark_connection_error does nothing if already set."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        callback = MagicMock()
        client._connection_error_callback = callback
        client._connection_error = True

        client.mark_connection_error()

        callback.assert_not_called()


class TestESPLocalCtrlClientTcpKeepalive:
    """Test ESPLocalCtrlClient TCP keepalive methods."""

    async def test_enable_tcp_keepalive_no_transport(self) -> None:
        """Test _enable_tcp_keepalive does nothing without transport."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = None

        # Should not raise
        await client._enable_tcp_keepalive()

    async def test_enable_tcp_keepalive_no_conn(self) -> None:
        """Test _enable_tcp_keepalive does nothing without conn."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock(spec=[])  # No conn attribute

        # Should not raise
        await client._enable_tcp_keepalive()

    async def test_enable_tcp_keepalive_success(self) -> None:
        """Test _enable_tcp_keepalive sets socket options."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_sock = MagicMock()
        mock_sock.setsockopt = MagicMock()
        client.transport = MagicMock()
        client.transport.conn = MagicMock()
        client.transport.conn.sock = mock_sock

        await client._enable_tcp_keepalive()

        # Should have called setsockopt for SO_KEEPALIVE
        mock_sock.setsockopt.assert_called()


class TestESPLocalCtrlClientMessageListener:
    """Test ESPLocalCtrlClient message listener methods."""

    async def test_start_message_listener_already_running(self) -> None:
        """Test _start_message_listener does nothing if already running."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        original_listener = MagicMock()
        client._http_listener = original_listener

        await client._start_message_listener()
        # Should not create new listener - existing listener should be preserved
        assert client._http_listener is original_listener

    async def test_start_message_listener_no_transport(self) -> None:
        """Test _start_message_listener does nothing without transport."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = None
        client._http_listener = None

        await client._start_message_listener()
        assert client._http_listener is None

    async def test_stop_message_listener_no_listener(self) -> None:
        """Test _stop_message_listener does nothing if no listener."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._http_listener = None

        # Should not raise
        await client._stop_message_listener()

    async def test_stop_message_listener_success(self) -> None:
        """Test _stop_message_listener stops listener."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_listener = MagicMock()
        mock_listener.stop = AsyncMock()
        client._http_listener = mock_listener

        await client._stop_message_listener()

        mock_listener.stop.assert_called_once()
        assert client._http_listener is None


class TestESPLocalCtrlClientCleanupSession:
    """Test ESPLocalCtrlClient _cleanup_session method."""

    async def test_cleanup_session_stops_listener(self) -> None:
        """Test _cleanup_session stops message listener."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )

        with patch.object(
            client, "_stop_message_listener", new_callable=AsyncMock
        ) as mock_stop:
            await client._cleanup_session()
            mock_stop.assert_called_once()

    async def test_cleanup_session_closes_transport(self) -> None:
        """Test _cleanup_session closes transport."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        client.transport = mock_transport
        client.session_established = True

        with patch.object(client, "_stop_message_listener", new_callable=AsyncMock):
            await client._cleanup_session()

            assert client.transport is None
            assert client.session_established is False

    async def test_cleanup_session_resets_state(self) -> None:
        """Test _cleanup_session resets all state."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.session_established = True
        client.security_ctx = MagicMock()

        with patch.object(client, "_stop_message_listener", new_callable=AsyncMock):
            await client._cleanup_session()

            assert client.session_established is False
            assert client.security_ctx is None


class TestESPLocalCtrlClientIsConnected:
    """Test ESPLocalCtrlClient is_connected method."""

    async def test_is_connected_with_healthy_socket(self) -> None:
        """Test is_connected returns True with healthy socket."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.is_socket_healthy = MagicMock(return_value=True)
        client.session_established = True
        client._connection_error = False

        result = await client.is_connected()
        assert result is True

    async def test_is_connected_with_unhealthy_socket(self) -> None:
        """Test is_connected returns False with unhealthy socket."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.is_socket_healthy = MagicMock(return_value=False)
        client.session_established = True
        client._connection_error = False

        result = await client.is_connected()
        assert result is False

    async def test_is_connected_socket_check_oserror(self) -> None:
        """Test is_connected returns False on socket check OSError."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.is_socket_healthy = MagicMock(side_effect=OSError())
        client.session_established = True
        client._connection_error = False

        result = await client.is_connected()
        assert result is False


class TestESPLocalCtrlClientGetAllPropertyValuesViaListener:
    """Test ESPLocalCtrlClient _get_all_property_values_via_listener method."""

    async def test_get_all_properties_no_transport(self) -> None:
        """Test returns empty list when no transport."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = None

        result = await client._get_all_property_values_via_listener()
        assert result == []

    async def test_get_all_properties_no_session(self) -> None:
        """Test returns empty list when no session."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = False

        result = await client._get_all_property_values_via_listener()
        assert result == []

    async def test_get_all_properties_no_listener(self) -> None:
        """Test returns empty list when no listener."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client._http_listener = None

        result = await client._get_all_property_values_via_listener()
        assert result == []

    async def test_get_all_properties_count_query_fails(self) -> None:
        """Test returns empty list when count query fails."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()

        mock_listener = MagicMock()
        mock_listener.send_query_and_wait = AsyncMock(return_value=(None, None))
        client._http_listener = mock_listener

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl_codec"
        ) as mock_proto:
            mock_proto.get_prop_count_request = MagicMock(return_value=b"request")

            result = await client._get_all_property_values_via_listener()
            assert result == []

    async def test_get_all_properties_zero_count(self) -> None:
        """Test returns empty list when count is zero."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()

        mock_listener = MagicMock()
        client._http_listener = mock_listener

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl_codec"
        ) as mock_proto:
            mock_proto.get_prop_count_request = MagicMock(return_value=b"request")

            with patch(
                "custom_components.esp_weaver.iot.client.client.MessageSource"
            ) as mock_source:
                mock_source.QUERY_RESPONSE = MagicMock()
                mock_listener.send_query_and_wait = AsyncMock(
                    return_value=(mock_source.QUERY_RESPONSE, {"count": 0})
                )

                result = await client._get_all_property_values_via_listener()
                assert result == []

    async def test_get_all_properties_handles_oserror(self) -> None:
        """Test handles OSError gracefully."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()

        mock_listener = MagicMock()
        mock_listener.send_query_and_wait = AsyncMock(side_effect=OSError())
        client._http_listener = mock_listener

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl_codec"
        ) as mock_proto:
            mock_proto.get_prop_count_request = MagicMock(return_value=b"request")

            result = await client._get_all_property_values_via_listener()
            assert result == []


class TestESPLocalCtrlClientStartMessageListener:
    """Test ESPLocalCtrlClient _start_message_listener full path."""

    async def test_start_message_listener_creates_listener(self) -> None:
        """Test _start_message_listener creates HTTPMessageListener."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.transport.session = MagicMock()
        client.transport.conn = MagicMock()
        client.security_ctx = MagicMock()
        client._http_listener = None
        client._message_callbacks = [MagicMock()]

        with patch(
            "custom_components.esp_weaver.iot.client.client.HTTPMessageListener"
        ) as mock_listener_class:
            mock_listener = MagicMock()
            mock_listener.start = AsyncMock()
            mock_listener_class.return_value = mock_listener

            await client._start_message_listener()

            mock_listener_class.assert_called_once()
            mock_listener.start.assert_called_once()
            assert client._http_listener == mock_listener


class TestESPLocalCtrlClientGetPropertyValuesSuccess:
    """Test ESPLocalCtrlClient get_property_values full success path."""

    async def test_get_property_values_success(self) -> None:
        """Test successful property values retrieval."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._connection_error = False

        mock_listener = MagicMock()
        # First call for count, second call for props
        mock_listener.send_query_and_wait = AsyncMock(
            side_effect=[
                (MessageSource.QUERY_RESPONSE, {"count": 1}),
                (MessageSource.QUERY_RESPONSE, {"properties": [{"name": "test"}]}),
            ]
        )
        client._http_listener = mock_listener

        result = await client.get_property_values()

        assert result == [{"name": "test"}]


class TestESPLocalCtrlClientConnectFullPath:
    """Test ESPLocalCtrlClient connect full execution path."""

    async def test_connect_full_success(self) -> None:
        """Test full successful connection."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
            pop="test_pop",
            security_mode=1,
        )

        mock_transport = MagicMock()
        mock_security_ctx = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl.get_transport",
                new_callable=AsyncMock,
            ) as mock_get_transport,
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl.get_security",
            ) as mock_get_security,
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl.establish_session",
                new_callable=AsyncMock,
            ) as mock_establish,
            patch.object(client, "_enable_tcp_keepalive", new_callable=AsyncMock),
            patch.object(client, "_start_message_listener", new_callable=AsyncMock),
        ):
            mock_get_transport.return_value = mock_transport
            mock_get_security.return_value = mock_security_ctx
            mock_establish.return_value = True

            result = await client.connect()

            assert result is True
            assert client.session_established is True

    async def test_connect_security_mode_0(self) -> None:
        """Test connection with security mode 0."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
            security_mode=0,
        )

        mock_transport = MagicMock()
        mock_security_ctx = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl.get_transport",
                new_callable=AsyncMock,
            ) as mock_get_transport,
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl.get_security",
            ) as mock_get_security,
            patch(
                "custom_components.esp_weaver.iot.client.client.local_ctrl.establish_session",
                new_callable=AsyncMock,
            ) as mock_establish,
            patch.object(client, "_enable_tcp_keepalive", new_callable=AsyncMock),
            patch.object(client, "_start_message_listener", new_callable=AsyncMock),
        ):
            mock_get_transport.return_value = mock_transport
            mock_get_security.return_value = mock_security_ctx
            mock_establish.return_value = True

            result = await client.connect()

            assert result is True


class TestESPLocalCtrlClientDisconnectCleanup:
    """Test ESPLocalCtrlClient disconnect cleanup scenarios."""

    async def test_disconnect_success(self) -> None:
        """Test successful disconnect."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        client.transport = mock_transport
        client.session_established = True

        with patch.object(client, "_stop_message_listener", new_callable=AsyncMock):
            await client.disconnect()

        assert client.transport is None
        assert client.session_established is False

    async def test_disconnect_handles_exception(self) -> None:
        """Test disconnect handles exception in cleanup."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        mock_transport = MagicMock()
        mock_transport.close = MagicMock(side_effect=Exception("Close error"))
        client.transport = mock_transport

        with patch.object(client, "_stop_message_listener", new_callable=AsyncMock):
            # Should not raise
            await client.disconnect()


class TestESPLocalCtrlClientSetPropertyValuesFullPath:
    """Test ESPLocalCtrlClient set_property_values full path."""

    async def test_set_property_values_connection_error_reconnect(self) -> None:
        """Test set_property_values when connection error triggers reconnect."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = True
        client.transport = MagicMock()
        client.session_established = False

        result = await client.set_property_values([0], [{"power": True}])

        assert result is False

    async def test_set_property_values_device_rejects(self) -> None:
        """Test set_property_values when device rejects request (line 438)."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._http_listener = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            # Device returns False (rejects the request)
            mock_esp.set_property_values = AsyncMock(return_value=False)

            result = await client.set_property_values([1], [{"power": True}])

        assert result is False

    async def test_set_property_values_oserror(self) -> None:
        """Test set_property_values handles OSError (lines 442-449)."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._http_listener = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.set_property_values = AsyncMock(
                side_effect=OSError("Network error")
            )

            result = await client.set_property_values([1], [{"power": True}])

        assert result is False
        assert client._connection_error is True

    async def test_set_property_values_with_listener(self) -> None:
        """Test set_property_values via listener path."""
        client = ESPLocalCtrlClient(
            node_id="test_node",
            ip="192.168.1.100",
        )
        client._connection_error = False
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()

        mock_listener = MagicMock()
        client._http_listener = mock_listener

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl.set_property_values",
            new_callable=AsyncMock,
        ) as mock_set_props:
            mock_set_props.return_value = True

            result = await client.set_property_values([0], [{"power": True}])

            assert result is True
            mock_set_props.assert_called_once()
