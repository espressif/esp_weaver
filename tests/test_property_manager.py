# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the property_manager module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.client.client import ESPLocalCtrlClient
from custom_components.esp_weaver.iot.managers.property_manager import (
    ConnectionCallbacks,
    PropertyManager,
)


class TestConnectionCallbacks:
    """Test ConnectionCallbacks dataclass."""

    def test_creation(self) -> None:
        """Test creating connection callbacks."""
        establish = AsyncMock()
        reconnect = AsyncMock()

        callbacks = ConnectionCallbacks(
            establish_connection=establish,
            reconnect_and_retry=reconnect,
        )

        assert callbacks.establish_connection == establish
        assert callbacks.reconnect_and_retry == reconnect


class TestPropertyManagerInit:
    """Test PropertyManager initialization."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with default_port for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    def test_basic_init(self, mock_hass: MagicMock, mock_registry: MagicMock) -> None:
        """Test basic initialization."""
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        assert manager.hass == mock_hass
        assert manager.domain == "esp_weaver"
        assert manager.registry == mock_registry
        assert manager._event_dispatcher is None
        assert manager._connection_callbacks is None

    def test_init_with_dispatcher(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test initialization with event dispatcher."""
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )

        assert manager._event_dispatcher == dispatcher

    def test_init_with_callbacks(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test initialization with connection callbacks."""
        callbacks = ConnectionCallbacks(
            establish_connection=AsyncMock(),
            reconnect_and_retry=AsyncMock(),
        )
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, connection_callbacks=callbacks
        )

        assert manager._connection_callbacks == callbacks

    def test_set_connection_callbacks(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test setting connection callbacks after initialization."""
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)
        callbacks = ConnectionCallbacks(
            establish_connection=AsyncMock(),
            reconnect_and_retry=AsyncMock(),
        )

        manager.set_connection_callbacks(callbacks)

        assert manager._connection_callbacks == callbacks


class TestPropertyManagerSetProperty:
    """Test PropertyManager.set_property method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with has_client for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        registry.has_client.return_value = True
        return registry

    @pytest.fixture
    def mock_client(self) -> MagicMock:
        """Create mock ESP client."""
        client = MagicMock(spec=ESPLocalCtrlClient)
        client.set_property_values = AsyncMock(return_value=True)
        return client

    async def test_set_property_success(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """Test successful property set."""
        mock_registry.get_client.return_value = mock_client
        mock_registry.get_device.return_value = MagicMock()
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager.set_property("node123", "power", True)

        assert result is True
        mock_client.set_property_values.assert_called_once()

    async def test_set_property_no_client_no_callbacks(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test set_property fails without client and callbacks."""
        mock_registry.has_client.return_value = False
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager.set_property("node123", "power", True)

        assert result is False

    async def test_set_property_establishes_connection(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """Test set_property establishes connection if needed."""
        mock_registry.has_client.return_value = False
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.100"
        mock_device.port = 8080
        mock_registry.get_device.return_value = mock_device

        establish_mock = AsyncMock(return_value=(True, None))
        callbacks = ConnectionCallbacks(
            establish_connection=establish_mock,
            reconnect_and_retry=AsyncMock(),
        )

        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, connection_callbacks=callbacks
        )

        # After connection, has_client returns True and get_client returns client
        mock_registry.has_client.side_effect = [False, True]
        mock_registry.get_client.return_value = mock_client

        result = await manager.set_property("node123", "power", True)

        establish_mock.assert_called_once()
        assert result is True
        mock_client.set_property_values.assert_called_once()

    async def test_set_property_fires_event(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """Test set_property fires update event on success."""
        mock_registry.get_client.return_value = mock_client
        mock_registry.get_device.return_value = MagicMock()
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )

        result = await manager.set_property("node123", "power", True)

        assert result is True
        # Event uses ESP device format (nested structure from build_device_command)
        dispatcher.assert_called_once_with("node123", {"Light": {"Power": True}})


class TestPropertyManagerTrySetProperty:
    """Test PropertyManager._try_set_property method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with default_port for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    async def test_no_client_returns_failed(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test returns failed when no client."""
        mock_registry.get_client.return_value = None
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager._try_set_property("node123", "power", True)

        assert result == "failed"

    async def test_invalid_param_returns_failed(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test returns failed for unrecognized parameter."""
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager._try_set_property(
            "node123", "completely_unknown_param", True
        )

        assert result == "failed"

    async def test_set_property_failure_returns_reconnect(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test set_property returning False triggers reconnect."""
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        # set_property_values returns False = failure
        mock_client.set_property_values = AsyncMock(return_value=False)
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager._try_set_property("node123", "power", True)

        assert result == "reconnect"

    async def test_network_error_returns_reconnect(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test network error returns reconnect."""
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.set_property_values = AsyncMock(
            side_effect=OSError("Network error")
        )
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager._try_set_property("node123", "power", True)

        assert result == "reconnect"


class TestPropertyManagerProcessPropertyUpdate:
    """Test PropertyManager.process_property_update method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    def test_process_with_dispatcher(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing property update fires event."""
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )

        manager.process_property_update("node123", {"Light": {"Power": True}})

        dispatcher.assert_called_once_with("node123", {"Light": {"Power": True}})

    def test_process_without_dispatcher(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing property update without dispatcher."""
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        # Should not raise
        manager.process_property_update("node123", {"Light": {"Power": True}})


class TestPropertyManagerMessageHandler:
    """Test PropertyManager.create_message_handler method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    def test_create_handler(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test creating message handler."""
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        handler = manager.create_message_handler("node123")

        assert callable(handler)

    async def test_handler_processes_active_report(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test handler processes active reports."""
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )
        handler = manager.create_message_handler("node123")

        # Mock MessageSource
        with patch(
            "custom_components.esp_weaver.iot.managers.property_manager.local_ctrl"
        ) as mock_esp:
            mock_esp.MessageSource.ACTIVE_REPORT = "ACTIVE_REPORT"
            mock_esp.MessageSource.QUERY_RESPONSE = "QUERY_RESPONSE"

            data = {
                "properties": [
                    {
                        "name": "params",
                        "value": b'{"Light": {"Power": true}}',
                    }
                ]
            }

            await handler("ACTIVE_REPORT", data)

            # Dispatcher should be called
            dispatcher.assert_called_once()

    async def test_handler_ignores_query_response(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test handler ignores query responses."""
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )
        handler = manager.create_message_handler("node123")

        with patch(
            "custom_components.esp_weaver.iot.managers.property_manager.local_ctrl"
        ) as mock_esp:
            mock_esp.MessageSource.ACTIVE_REPORT = "ACTIVE_REPORT"
            mock_esp.MessageSource.QUERY_RESPONSE = "QUERY_RESPONSE"

            await handler("QUERY_RESPONSE", {"count": 2})

            # Dispatcher should not be called for query response
            dispatcher.assert_not_called()


class TestPropertyManagerProcessActiveReport:
    """Test PropertyManager.process_active_report method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    async def test_process_valid_properties(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing valid properties."""
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )

        properties = [
            {
                "name": "params",
                "value": b'{"Light": {"Power": true, "Brightness": 100}}',
            }
        ]

        await manager.process_active_report("node123", properties)

        dispatcher.assert_called_once()
        call_args = dispatcher.call_args[0]
        assert call_args[0] == "node123"
        assert call_args[1] == {"Light": {"Power": True, "Brightness": 100}}

    async def test_process_empty_properties(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing empty properties."""
        dispatcher = MagicMock()
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, event_dispatcher=dispatcher
        )

        await manager.process_active_report("node123", [])

        dispatcher.assert_not_called()

    async def test_process_with_custom_callback(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing with custom callback."""
        custom_callback = MagicMock()
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        properties = [{"name": "params", "value": b'{"Light": {"Power": true}}'}]

        await manager.process_active_report("node123", properties, custom_callback)

        custom_callback.assert_called_once()
        call_args = custom_callback.call_args[0]
        assert call_args[0] == "node123"
        assert call_args[1] == {"Light": {"Power": True}}


class TestPropertyManagerEdgeCases:
    """Test PropertyManager edge cases and error handling."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    async def test_set_property_no_callbacks_for_reconnect(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test reconnect_and_retry returns False when no callbacks configured."""
        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)
        # No connection callbacks set

        result = await manager._reconnect_and_retry("node123", "power", True)

        assert result is False

    async def test_set_property_device_not_found(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test establish_connection returns False when device not found."""
        mock_registry.get_device.return_value = None
        callbacks = ConnectionCallbacks(
            establish_connection=AsyncMock(),
            reconnect_and_retry=AsyncMock(),
        )
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, connection_callbacks=callbacks
        )

        result = await manager._establish_connection_for_set("unknown_node")

        assert result is False

    async def test_set_property_device_missing_ip_or_port(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test establish_connection returns False when device missing IP or port."""
        mock_device = MagicMock()
        mock_device.ip = None
        mock_device.port = 8080
        mock_registry.get_device.return_value = mock_device

        callbacks = ConnectionCallbacks(
            establish_connection=AsyncMock(),
            reconnect_and_retry=AsyncMock(),
        )
        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, connection_callbacks=callbacks
        )

        result = await manager._establish_connection_for_set("node123")

        assert result is False

    async def test_try_set_property_timeout_returns_reconnect(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test _try_set_property returns RECONNECT on timeout."""
        # Mock client that times out
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.set_property_values = AsyncMock(side_effect=TimeoutError())
        mock_registry.get_client.return_value = mock_client

        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager._try_set_property("node123", "power", True)

        # Should return RECONNECT result
        assert result == "reconnect"

    async def test_try_set_property_value_error_returns_failed(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test _try_set_property returns FAILED on ValueError."""
        # Mock client that raises ValueError
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.set_property_values = AsyncMock(
            side_effect=ValueError("Invalid value")
        )
        mock_registry.get_client.return_value = mock_client

        manager = PropertyManager(mock_hass, "esp_weaver", mock_registry)

        result = await manager._try_set_property("node123", "power", True)

        # Should return FAILED result
        assert result == "failed"

    async def test_set_property_reconnect_path(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test that RECONNECT result triggers reconnect_and_retry callback."""
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.100"
        mock_device.port = 8080
        mock_registry.get_device.return_value = mock_device

        # First call times out, triggering reconnect path
        mock_client = MagicMock(spec=ESPLocalCtrlClient)
        mock_client.set_property_values = AsyncMock(side_effect=TimeoutError())
        mock_registry.get_client.return_value = mock_client

        reconnect_mock = AsyncMock(return_value=True)
        callbacks = ConnectionCallbacks(
            establish_connection=AsyncMock(return_value=(True, mock_client)),
            reconnect_and_retry=reconnect_mock,
        )

        manager = PropertyManager(
            mock_hass, "esp_weaver", mock_registry, connection_callbacks=callbacks
        )

        result = await manager.set_property("node123", "power", True)

        # reconnect_and_retry should have been called
        reconnect_mock.assert_called_once_with("node123", "power", True)
        assert result is True
