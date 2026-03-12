# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver connection manager module."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.iot.client.client import ESPLocalCtrlClient
from custom_components.esp_weaver.iot.managers.connection_manager import (
    ConnectionManager,
)
from custom_components.esp_weaver.iot.managers.device_registry import DeviceRegistry


@pytest.fixture
def registry() -> DeviceRegistry:
    """Create a DeviceRegistry instance."""
    return DeviceRegistry()


@pytest.fixture
def manager(hass: HomeAssistant, registry: DeviceRegistry) -> ConnectionManager:
    """Create a ConnectionManager instance."""
    return ConnectionManager(
        hass=hass,
        domain="esp_weaver",
        registry=registry,
    )


class TestConnectionManagerInit:
    """Test ConnectionManager initialization."""

    def test_basic_initialization(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test basic connection manager initialization."""
        registry = DeviceRegistry()

        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        assert manager.hass == hass
        assert manager.domain == "esp_weaver"
        assert manager.registry == registry

    def test_initialization_with_callbacks(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test initialization with callback functions."""
        registry = DeviceRegistry()

        def message_handler_factory(node_id):
            return MagicMock()

        def property_extractor(properties):
            return {}

        def property_processor(node_id, values):
            pass

        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
            message_handler_factory=message_handler_factory,
            property_extractor=property_extractor,
            property_processor=property_processor,
        )

        assert manager._message_handler_factory == message_handler_factory
        assert manager._property_extractor == property_extractor
        assert manager._property_processor == property_processor


class TestConnectionManagerErrorHandling:
    """Test ConnectionManager error handling."""

    async def test_on_connection_error_fires_event(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test connection error fires HA event."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        # Capture events using async_capture_events or listening
        events: list = []

        async def capture_event(event):
            events.append(event)

        hass.bus.async_listen("esp_weaver_connection_error", capture_event)

        manager._on_connection_error("test_node")
        await hass.async_block_till_done()

        # Verify event was fired with correct arguments
        assert len(events) == 1
        assert events[0].data["node_id"] == "test_node"


class TestConnectionManagerSession:
    """Test ConnectionManager session management."""

    async def test_establish_session_with_existing_connected_client(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test establishing session when client already connected."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test establishing session reconnects disconnected client."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test manager uses registry to store clients."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
        registry: DeviceRegistry,
    ) -> None:
        """Test connect_and_sync when already connected."""
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test connect_and_sync with new connection."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test connect_and_sync when connection fails."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test connect_and_sync processes properties when requested."""
        registry = DeviceRegistry()

        property_processor = MagicMock()
        property_extractor = MagicMock(return_value={"power": True})

        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
            property_extractor=property_extractor,
            property_processor=property_processor,
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
                return_value=[{"name": "params", "value": b'{"power": true}'}],
            ),
        ):
            await manager.connect_and_sync(
                "test_node", "192.168.1.100", 8080, process_properties=True
            )

            property_processor.assert_called_once()


class TestConnectionManagerFetchProperties:
    """Test ConnectionManager _fetch_device_properties method."""

    async def test_fetch_properties_no_client(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _fetch_device_properties returns None when no client."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        result = await manager._fetch_device_properties("test_node")
        assert result is None

    async def test_fetch_properties_success(
        self,
        hass: HomeAssistant,
        registry: DeviceRegistry,
    ) -> None:
        """Test _fetch_device_properties returns properties."""
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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

    async def test_fetch_properties_handles_oserror(
        self,
        hass: HomeAssistant,
        registry: DeviceRegistry,
    ) -> None:
        """Test _fetch_device_properties handles OSError."""
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        # Use MagicMock with spec to avoid side effects from real client constructor
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.get_property_values = AsyncMock(side_effect=OSError())
        # Add ip and port for logging
        mock_client.ip = "192.168.1.100"
        mock_client.port = 8080

        registry.set_client("test_node", mock_client)

        result = await manager._fetch_device_properties("test_node")
        assert result is None


class TestConnectionManagerProcessProperties:
    """Test ConnectionManager _process_fetched_properties method."""

    def test_process_properties_no_callbacks(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _process_fetched_properties with no callbacks."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        # Should not raise
        manager._process_fetched_properties("test_node", [])

    def test_process_properties_calls_callbacks(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _process_fetched_properties calls callbacks."""
        registry = DeviceRegistry()

        property_processor = MagicMock()
        property_extractor = MagicMock(return_value={"power": True})

        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
            property_extractor=property_extractor,
            property_processor=property_processor,
        )

        manager._process_fetched_properties(
            "test_node",
            [{"name": "params", "value": b"{}"}],
        )

        property_extractor.assert_called_once()
        property_processor.assert_called_once_with("test_node", {"power": True})

    def test_process_properties_no_values(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _process_fetched_properties with no values from extractor."""
        registry = DeviceRegistry()

        property_processor = MagicMock()
        property_extractor = MagicMock(return_value=None)

        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
            property_extractor=property_extractor,
            property_processor=property_processor,
        )

        manager._process_fetched_properties("test_node", [])

        property_processor.assert_not_called()


class TestConnectionManagerDisconnect:
    """Test ConnectionManager disconnect methods."""

    async def test_disconnect_device_no_client(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test disconnect_device when no client exists."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        # Should not raise
        await manager.disconnect_device("test_node")

    async def test_disconnect_device_success(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test disconnect_device success."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        registry.set_client("test_node", mock_client)

        await manager.disconnect_device("test_node")

        mock_client.disconnect.assert_awaited_once()

    async def test_disconnect_device_handles_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test disconnect_device handles errors gracefully."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock(side_effect=OSError())
        registry.set_client("test_node", mock_client)

        # Should not raise
        await manager.disconnect_device("test_node")

    async def test_disconnect_all(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test disconnect_all disconnects all clients."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test _get_device_config returns config when found."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        # Create mock config entry with both pop and security_version
        mock_entry = MagicMock()
        mock_entry.data = {
            "node_id": "test_node",
            "custom_pop": "secret_pop",
            "security_version": 0,
        }

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            pop, security_version = manager._get_device_config("test_node")
            assert pop == "secret_pop"
            assert security_version == 0

    def test_get_device_config_not_found(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _get_device_config returns defaults when not found."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[],
        ):
            pop, security_version = manager._get_device_config("test_node")
            assert pop == ""
            assert security_version == 1

    def test_get_device_config_partial(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _get_device_config returns defaults for missing fields."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        # Entry with only node_id, no custom_pop or security_version
        mock_entry = MagicMock()
        mock_entry.data = {
            "node_id": "test_node",
        }

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            pop, security_version = manager._get_device_config("test_node")
            assert pop == ""
            assert security_version == 1


class TestConnectionManagerEstablishSession:
    """Test ConnectionManager _establish_session method."""

    async def test_establish_session_connection_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _establish_session handles ConnectionError."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_class:
            mock_class.side_effect = ConnectionError("Connection refused")

            result = await manager._establish_session(
                "test_node", "192.168.1.100", 8080
            )

            assert result is False

    async def test_establish_session_runtime_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _establish_session handles RuntimeError."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
            registry=registry,
        )

        with patch(
            "custom_components.esp_weaver.iot.managers.connection_manager.ESPLocalCtrlClient"
        ) as mock_class:
            mock_class.side_effect = RuntimeError("Protocol error")

            result = await manager._establish_session(
                "test_node", "192.168.1.100", 8080
            )

            assert result is False

    async def test_establish_session_connect_failure(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test _establish_session when client connect fails."""
        registry = DeviceRegistry()
        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
        hass: HomeAssistant,
    ) -> None:
        """Test _establish_session success with message handler."""
        registry = DeviceRegistry()

        message_handler = MagicMock()
        message_handler_factory = MagicMock(return_value=message_handler)

        manager = ConnectionManager(
            hass=hass,
            domain="esp_weaver",
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
