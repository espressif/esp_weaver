# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver connection manager module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.client.client import ESPLocalCtrlClient
from custom_components.esp_weaver.iot.managers.connection_manager import (
    ConnectionManager,
)
from custom_components.esp_weaver.iot.managers.device_registry import DeviceRegistry
from tests.fakes import FakeHost


@pytest.fixture
def host() -> FakeHost:
    """A host that records what the manager tells it."""
    return FakeHost()


@pytest.fixture
def registry() -> DeviceRegistry:
    """Create a DeviceRegistry instance."""
    return DeviceRegistry()


@pytest.fixture
def manager(host: FakeHost, registry: DeviceRegistry) -> ConnectionManager:
    """Create a ConnectionManager instance."""
    return ConnectionManager(
        host=host,
        registry=registry,
    )


class TestConnectionManagerInit:
    """Test ConnectionManager initialization."""

    def test_basic_initialization(
        self,
        host: FakeHost,
    ) -> None:
        """Test basic connection manager initialization."""
        registry = DeviceRegistry()

        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        assert manager.host is host
        assert manager.registry == registry

    def test_initialization_with_callbacks(
        self,
        host: FakeHost,
    ) -> None:
        """Test initialization with callback functions."""
        registry = DeviceRegistry()

        def message_handler_factory(node_id):
            return MagicMock()

        def on_properties(node_id, properties):
            pass

        manager = ConnectionManager(
            host=host,
            registry=registry,
            message_handler_factory=message_handler_factory,
            on_properties=on_properties,
        )

        assert manager._message_handler_factory == message_handler_factory
        assert manager._on_properties == on_properties


class TestConnectionManagerErrorHandling:
    """Test ConnectionManager error handling."""

    def test_on_connection_error_tells_the_host(self, host: FakeHost) -> None:
        """Test a connection error is reported to the host for that node only."""
        manager = ConnectionManager(host=host, registry=DeviceRegistry())

        manager._on_connection_error("test_node")

        assert host.lost == ["test_node"]


class TestConnectionManagerSession:
    """Test ConnectionManager session management."""

    async def test_establish_session_with_existing_connected_client(
        self,
        host: FakeHost,
    ) -> None:
        """Test establishing session when client already connected."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Create mock client that is connected
        mock_client = MagicMock()
        mock_client.is_connected = AsyncMock(return_value=True)
        registry.set_client("test_node", mock_client)

        result = await manager._establish_session(
            node_id="test_node",
            ip="192.168.1.100",
            port=8080,
        )

        assert result is True

    async def test_establish_session_reconnects_disconnected_client(
        self,
        host: FakeHost,
    ) -> None:
        """Test establishing session reconnects disconnected client."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Create mock client that is disconnected
        mock_client = MagicMock()
        mock_client.is_connected = AsyncMock(return_value=False)
        mock_client.disconnect = AsyncMock()
        registry.set_client("test_node", mock_client)

        # Mock the client creation
        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_client_class:
            # Use MagicMock for sync methods, configure async methods explicitly
            mock_new_client = MagicMock()
            mock_new_client.connect = AsyncMock(return_value=False)
            mock_new_client.disconnect = AsyncMock()
            mock_new_client.add_message_callback = MagicMock()
            mock_new_client.set_connection_error_callback = MagicMock()
            mock_client_class.return_value = mock_new_client

            result = await manager._establish_session(
                node_id="test_node",
                ip="192.168.1.100",
                port=8080,
            )

            # Old client should have been disconnected
            mock_client.disconnect.assert_awaited_once()
            # Connect returned False, so session establishment should fail
            assert result is False


class TestConnectionManagerDeviceRegistry:
    """Test ConnectionManager with DeviceRegistry."""

    def test_uses_registry_for_client_storage(
        self,
        host: FakeHost,
    ) -> None:
        """Test manager uses registry to store clients."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Register a device via registry
        registry.register_device("test_node", "192.168.1.100")

        # Verify device is accessible through registry
        device = registry.get_device("test_node")
        assert device is not None
        assert device.ip == "192.168.1.100"

        # Verify manager can access the same registry
        assert manager.registry is registry
        assert manager.registry.get_device("test_node") is device


class TestConnectionManagerConnectAndSync:
    """Test ConnectionManager connect_and_sync method."""

    async def test_connect_and_sync_already_connected(
        self,
        host: FakeHost,
        registry: DeviceRegistry,
    ) -> None:
        """Test connect_and_sync when already connected."""
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Use MagicMock with spec to avoid side effects from real client constructor
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.is_connected = AsyncMock(return_value=True)
        mock_client.get_property_values = AsyncMock(
            return_value=[{"name": "config", "value": b"{}"}]
        )
        registry.set_client("test_node", mock_client)

        success, properties = await manager.connect_and_sync(
            "test_node", "192.168.1.100", 8080
        )

        assert success is True
        assert properties is not None

    async def test_connect_and_sync_new_connection(
        self,
        host: FakeHost,
    ) -> None:
        """Test connect_and_sync with new connection."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        with (
            patch.object(
                manager,
                "_establish_session",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                manager,
                "_fetch_device_properties",
                new_callable=AsyncMock,
                return_value=[{"name": "config"}],
            ),
        ):
            success, _properties = await manager.connect_and_sync(
                "test_node", "192.168.1.100", 8080
            )

            assert success is True

    async def test_connect_and_sync_connection_failure(
        self,
        host: FakeHost,
    ) -> None:
        """Test connect_and_sync when connection fails."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        with patch.object(
            manager,
            "_establish_session",
            new_callable=AsyncMock,
            return_value=False,
        ):
            success, properties = await manager.connect_and_sync(
                "test_node", "192.168.1.100", 8080
            )

            assert success is False
            assert properties is None

    async def test_connect_and_sync_processes_properties(
        self,
        host: FakeHost,
    ) -> None:
        """Test connect_and_sync processes properties when requested."""
        registry = DeviceRegistry()

        on_properties = AsyncMock()
        properties = [{"name": "params", "value": b'{"power": true}'}]

        manager = ConnectionManager(
            host=host,
            registry=registry,
            on_properties=on_properties,
        )

        with (
            patch.object(
                manager,
                "_establish_session",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                manager,
                "_fetch_device_properties",
                new_callable=AsyncMock,
                return_value=properties,
            ),
        ):
            await manager.connect_and_sync(
                "test_node", "192.168.1.100", 8080, process_properties=True
            )

            on_properties.assert_awaited_once_with("test_node", properties)


class TestConnectionManagerFetchProperties:
    """Test ConnectionManager _fetch_device_properties method."""

    async def test_fetch_properties_no_client(
        self,
        host: FakeHost,
    ) -> None:
        """Test _fetch_device_properties returns None when no client."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        result = await manager._fetch_device_properties("test_node")
        assert result is None

    async def test_fetch_properties_success(
        self,
        host: FakeHost,
        registry: DeviceRegistry,
    ) -> None:
        """Test _fetch_device_properties returns properties."""
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Use MagicMock with spec to avoid side effects from real client constructor
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.get_property_values = AsyncMock(
            return_value=[{"name": "config", "value": b"{}"}]
        )

        registry.set_client("test_node", mock_client)

        result = await manager._fetch_device_properties("test_node")
        assert result is not None

    async def test_fetch_properties_passes_an_unanswered_read_through(
        self,
        host: FakeHost,
        registry: DeviceRegistry,
    ) -> None:
        """The client answers None for a node it could not ask; so does the fetch."""
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.get_property_values = AsyncMock(return_value=None)
        registry.set_client("test_node", mock_client)

        result = await manager._fetch_device_properties("test_node")
        assert result is None


class TestConnectionManagerProcessProperties:
    """Test what connect_and_sync does with the properties it reads back."""

    async def test_process_properties_no_callbacks(
        self,
        host: FakeHost,
    ) -> None:
        """A manager nobody wired up still connects and reads; it just tells no one."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )
        properties = [{"name": "params", "value": b"{}"}]

        with (
            patch.object(
                manager,
                "_establish_session",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                manager,
                "_fetch_device_properties",
                new_callable=AsyncMock,
                return_value=properties,
            ),
        ):
            result = await manager.connect_and_sync(
                "test_node", "192.168.1.100", 8080, process_properties=True
            )

        assert result == (True, properties)

    async def test_nothing_fetched_reaches_nobody(
        self,
        host: FakeHost,
    ) -> None:
        """A read that came back empty is not handed on as a report."""
        registry = DeviceRegistry()
        on_properties = MagicMock()

        manager = ConnectionManager(
            host=host,
            registry=registry,
            on_properties=on_properties,
        )

        with (
            patch.object(
                manager,
                "_establish_session",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                manager,
                "_fetch_device_properties",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await manager.connect_and_sync(
                "test_node", "192.168.1.100", 8080, process_properties=True
            )

        assert result == (True, None)
        on_properties.assert_not_called()


class TestConnectionManagerDisconnect:
    """Test ConnectionManager disconnect methods."""

    async def test_disconnect_device_no_client(
        self,
        host: FakeHost,
    ) -> None:
        """Test disconnect_device when no client exists."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Should not raise
        await manager.disconnect_device("test_node")

    async def test_disconnect_device_success(
        self,
        host: FakeHost,
    ) -> None:
        """Test disconnect_device success."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        registry.set_client("test_node", mock_client)

        await manager.disconnect_device("test_node")

        mock_client.disconnect.assert_awaited_once()

    async def test_disconnect_device_handles_error(
        self,
        host: FakeHost,
    ) -> None:
        """Test disconnect_device handles errors gracefully."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock(side_effect=OSError())
        registry.set_client("test_node", mock_client)

        # Should not raise
        await manager.disconnect_device("test_node")

    async def test_disconnect_all(
        self,
        host: FakeHost,
    ) -> None:
        """Test disconnect_all disconnects all clients."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        mock_client1 = MagicMock()
        mock_client1.disconnect = AsyncMock()
        mock_client2 = MagicMock()
        mock_client2.disconnect = AsyncMock()

        registry.set_client("node1", mock_client1)
        registry.set_client("node2", mock_client2)

        await manager.disconnect_all()

        mock_client1.disconnect.assert_awaited_once()
        mock_client2.disconnect.assert_awaited_once()


class TestConnectionManagerConfigHelpers:
    """Test ConnectionManager configuration helper methods."""

    def test_get_device_config_found(
        self,
        host: FakeHost,
    ) -> None:
        """Test _get_device_config returns config when found."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
            device_auth=lambda node_id: (
                ("secret_pop", 0) if node_id == "test_node" else ("", 1)
            ),
        )

        pop, security_version = manager._get_device_config("test_node")
        assert pop == "secret_pop"
        assert security_version == 0

    def test_get_device_config_not_found(
        self,
        host: FakeHost,
    ) -> None:
        """Test _get_device_config returns defaults when not found."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        pop, security_version = manager._get_device_config("test_node")
        assert pop == ""
        assert security_version == 1

    def test_get_device_config_partial(
        self,
        host: FakeHost,
    ) -> None:
        """Test _get_device_config returns defaults for missing fields."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
            device_auth=lambda node_id: ("", 1) if node_id == "test_node" else ("x", 0),
        )

        pop, security_version = manager._get_device_config("test_node")
        assert pop == ""
        assert security_version == 1


class TestConnectionManagerEstablishSession:
    """Test ConnectionManager _establish_session method."""

    async def test_establish_session_connect_failure(
        self,
        host: FakeHost,
    ) -> None:
        """Test _establish_session when client connect fails."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_class:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=False)
            mock_client.disconnect = AsyncMock()
            mock_client.add_message_callback = MagicMock()
            mock_client.set_connection_error_callback = MagicMock()
            mock_class.return_value = mock_client

            result = await manager._establish_session(
                "test_node", "192.168.1.100", 8080
            )

            assert result is False
            mock_client.disconnect.assert_awaited_once()

    async def test_establish_session_success_with_callbacks(
        self,
        host: FakeHost,
    ) -> None:
        """Test _establish_session success with message handler."""
        registry = DeviceRegistry()

        message_handler = MagicMock()
        message_handler_factory = MagicMock(return_value=message_handler)

        manager = ConnectionManager(
            host=host,
            registry=registry,
            message_handler_factory=message_handler_factory,
        )

        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_class:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.add_message_callback = MagicMock()
            mock_client.set_connection_error_callback = MagicMock()
            mock_class.return_value = mock_client

            result = await manager._establish_session(
                "test_node", "192.168.1.100", 8080
            )

            assert result is True
            mock_client.add_message_callback.assert_called_once_with(message_handler)

    async def test_establish_session_existing_client_disconnect_error(
        self,
        host: FakeHost,
    ) -> None:
        """Test _establish_session handles disconnect error for existing client."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        # Create mock existing client that is disconnected and fails on disconnect
        mock_existing_client = MagicMock()
        mock_existing_client.is_connected = AsyncMock(return_value=False)
        mock_existing_client.disconnect = AsyncMock(
            side_effect=OSError("Disconnect failed")
        )
        registry.set_client("test_node", mock_existing_client)

        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_class:
            mock_new_client = MagicMock()
            mock_new_client.connect = AsyncMock(return_value=True)
            mock_new_client.add_message_callback = MagicMock()
            mock_new_client.set_connection_error_callback = MagicMock()
            mock_class.return_value = mock_new_client

            result = await manager._establish_session(
                "test_node", "192.168.1.100", 8080
            )

            mock_existing_client.disconnect.assert_awaited_once()
            assert result is True

    async def test_establish_session_existing_client_disconnect_runtime_error(
        self,
        host: FakeHost,
    ) -> None:
        """Test _establish_session handles RuntimeError on disconnect."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        mock_existing_client = MagicMock()
        mock_existing_client.is_connected = AsyncMock(return_value=False)
        mock_existing_client.disconnect = AsyncMock(
            side_effect=RuntimeError("Client in invalid state")
        )
        registry.set_client("test_node", mock_existing_client)

        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_class:
            mock_new_client = MagicMock()
            mock_new_client.connect = AsyncMock(return_value=True)
            mock_new_client.add_message_callback = MagicMock()
            mock_new_client.set_connection_error_callback = MagicMock()
            mock_class.return_value = mock_new_client

            result = await manager._establish_session(
                "test_node", "192.168.1.100", 8080
            )

            mock_existing_client.disconnect.assert_awaited_once()
            assert result is True


class TestConnectionManagerConnectAndSyncSkipProcessing:
    """Test ConnectionManager connect_and_sync with process_properties=False."""

    async def test_connect_and_sync_skip_processing_already_connected(
        self,
        host: FakeHost,
    ) -> None:
        """Test connect_and_sync with process_properties=False when already connected."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.is_connected = AsyncMock(return_value=True)
        registry.set_client("test_node", mock_client)

        success, properties = await manager.connect_and_sync(
            "test_node",
            "192.168.1.100",
            8080,
            process_properties=False,
        )

        assert success is True
        assert properties is None

    async def test_connect_and_sync_skip_processing_new_connection(
        self,
        host: FakeHost,
    ) -> None:
        """Test connect_and_sync with process_properties=False for new connection."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        with patch.object(
            manager,
            "_establish_session",
            new_callable=AsyncMock,
            return_value=True,
        ):
            success, properties = await manager.connect_and_sync(
                "test_node",
                "192.168.1.100",
                8080,
                process_properties=False,
            )

            assert success is True
            assert properties is None

    async def test_connect_and_sync_no_properties_fetched(
        self,
        host: FakeHost,
    ) -> None:
        """Test connect_and_sync when no properties are fetched (logs warning)."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            host=host,
            registry=registry,
        )

        with (
            patch.object(
                manager,
                "_establish_session",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                manager,
                "_fetch_device_properties",
                new_callable=AsyncMock,
                return_value=None,  # No properties fetched
            ),
        ):
            success, properties = await manager.connect_and_sync(
                "test_node",
                "192.168.1.100",
                8080,
                process_properties=True,
            )

            assert success is True
            assert properties is None
