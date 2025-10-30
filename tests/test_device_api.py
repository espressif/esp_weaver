# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver device API module."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.iot.client.device_api import ESPWeaverApi

# Test constants
TEST_NODE_ID = "test_node"
TEST_IP_ADDRESS = "192.168.1.100"
TEST_PORT = 8080
TEST_DOMAIN = "esp_weaver"
TEST_ENTRY_ID = "entry_123"


@pytest.fixture
def api(hass: HomeAssistant) -> ESPWeaverApi:
    """Create an ESPWeaverApi instance for testing."""
    return ESPWeaverApi(hass=hass, domain=TEST_DOMAIN)


class TestESPWeaverApiInit:
    """Test ESPWeaverApi initialization."""

    def test_basic_initialization(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test basic API initialization."""
        api = ESPWeaverApi(
            hass=hass,
            domain=TEST_DOMAIN,
        )

        assert api.hass == hass
        assert api.domain == TEST_DOMAIN
        assert api.default_port == TEST_PORT
        assert api.registry is not None

    def test_initialization_with_custom_port(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test API initialization with custom port."""
        api = ESPWeaverApi(
            hass=hass,
            domain=TEST_DOMAIN,
            default_port=9090,
        )

        assert api.default_port == 9090

    def test_initialization_with_event_dispatcher(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test API initialization with event dispatcher wiring."""
        dispatcher = MagicMock()
        api = ESPWeaverApi(
            hass=hass,
            domain=TEST_DOMAIN,
            event_dispatcher=dispatcher,
        )

        # Verify wiring by invoking the public API method that dispatches events
        test_params = {"test_key": "test_value"}
        api.process_property_update(TEST_NODE_ID, test_params)

        # Assert the dispatcher was invoked with expected args
        dispatcher.assert_called_once_with(TEST_NODE_ID, test_params)


class TestESPWeaverApiDeviceAvailability:
    """Test ESPWeaverApi device availability methods."""

    def test_is_device_available_sync(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_device_available sync method."""
        # Register a device and set up a mock client
        device = api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
        device.registered = True

        # Create mock client with required attributes
        mock_client = MagicMock()
        mock_client.session_established = True
        mock_client.transport = MagicMock()
        api.registry.set_client(TEST_NODE_ID, mock_client)

        result = api.is_device_available(TEST_NODE_ID)
        assert result is True

    def test_is_device_available_not_found(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_device_available for non-existent device."""
        result = api.is_device_available("non_existent")
        assert result is False

    async def test_is_device_available_async(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_device_available_async method."""
        device = api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
        device.registered = True

        # Create mock client with required attributes
        mock_client = MagicMock()
        mock_client.session_established = True
        mock_client.transport = MagicMock()
        mock_client.is_connected = AsyncMock(return_value=True)
        api.registry.set_client(TEST_NODE_ID, mock_client)

        result = await api.is_device_available_async(TEST_NODE_ID)
        assert result is True

    async def test_is_mdns_available(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_mdns_available method."""
        with patch.object(
            api._availability,
            "check_device_mdns_available",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_check:
            result = await api.is_mdns_available(TEST_NODE_ID, TEST_IP_ADDRESS)

            assert result is True
            mock_check.assert_called_once_with(TEST_NODE_ID, TEST_IP_ADDRESS)


class TestESPWeaverApiEntityDiscovery:
    """Test ESPWeaverApi entity discovery methods."""

    def test_is_discovery_completed(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_discovery_completed method."""
        # Initially not completed
        result = api.is_discovery_completed(TEST_NODE_ID)
        assert result is False

    def test_mark_discovery_completed(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test mark_discovery_completed method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        api.mark_discovery_completed(TEST_NODE_ID)

        result = api.is_discovery_completed(TEST_NODE_ID)
        assert result is True

    async def test_parse_and_discover_entities(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test parse_and_discover_entities method."""
        with patch.object(
            api._discovery,
            "parse_and_discover_entities",
            new_callable=AsyncMock,
        ) as mock_parse:
            await api.parse_and_discover_entities(
                TEST_NODE_ID,
                [{"name": "config", "value": b"{}"}],
                "Test Device",
            )

            mock_parse.assert_called_once_with(
                TEST_NODE_ID,
                [{"name": "config", "value": b"{}"}],
                "Test Device",
            )


class TestESPWeaverApiPropertyOperations:
    """Test ESPWeaverApi property operations."""

    async def test_set_local_ctrl_property(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test set_local_ctrl_property method."""
        with patch.object(
            api._property,
            "set_property",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_set:
            result = await api.set_local_ctrl_property(TEST_NODE_ID, "power", True)

            assert result is True
            mock_set.assert_called_once_with(TEST_NODE_ID, "power", True)

    def test_process_property_update(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test process_property_update method."""
        with patch.object(
            api._property,
            "process_property_update",
        ) as mock_process:
            api.process_property_update(TEST_NODE_ID, {"power": True})

            mock_process.assert_called_once_with(TEST_NODE_ID, {"power": True})


class TestESPWeaverApiDeviceRegistration:
    """Test ESPWeaverApi device registration methods."""

    def test_register_config_entry(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_config_entry method."""
        api.register_config_entry(TEST_NODE_ID, TEST_ENTRY_ID)

        entry_id = api.registry.get_config_entry_id(TEST_NODE_ID)
        assert entry_id == TEST_ENTRY_ID

    async def test_register_device_already_connected(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device when already connected."""
        # Register device first
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        # Create mock client that is connected
        mock_client = MagicMock()
        mock_client.is_connected = AsyncMock(return_value=True)
        api.registry.set_client(TEST_NODE_ID, mock_client)

        result = await api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        assert result is True

    async def test_register_device_new_connection(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device with new connection."""
        with patch.object(
            api,
            "_connect_and_sync_device",
            new_callable=AsyncMock,
            return_value=(True, []),
        ):
            result = await api.register_device(
                TEST_NODE_ID, TEST_IP_ADDRESS, port=TEST_PORT
            )

            assert result is True

    async def test_register_device_connection_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device when connection fails."""
        with patch.object(
            api,
            "_connect_and_sync_device",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = await api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

            assert result is False

    async def test_unregister_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test unregister_device method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        with patch.object(
            api._connection,
            "disconnect_device",
            new_callable=AsyncMock,
        ) as mock_disconnect:
            await api.unregister_device(TEST_NODE_ID)

            mock_disconnect.assert_called_once_with(TEST_NODE_ID)


class TestESPWeaverApiCleanup:
    """Test ESPWeaverApi cleanup methods."""

    async def test_cleanup_stops_browser(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test cleanup stops mDNS browser."""
        mock_browser = MagicMock()
        mock_browser.cancel = MagicMock()
        api._global_browser = mock_browser

        with patch.object(
            api._connection,
            "disconnect_all",
            new_callable=AsyncMock,
        ):
            await api.cleanup()

            mock_browser.cancel.assert_called_once()
            assert api._global_browser is None

    async def test_cleanup_disconnects_all(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test cleanup disconnects all devices."""
        with patch.object(
            api._connection,
            "disconnect_all",
            new_callable=AsyncMock,
        ) as mock_disconnect:
            await api.cleanup()

            mock_disconnect.assert_called_once()


class TestESPWeaverApiServices:
    """Test ESPWeaverApi service methods."""

    async def test_start_services_disabled(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test start_services with discovery disabled."""
        await api.start_services(enable_discovery=False)

        assert api._global_browser is None

    async def test_start_services_already_started(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test start_services when already started."""
        api._global_browser = MagicMock()

        await api.start_services(enable_discovery=True)

        # Should not have changed
        assert api._global_browser is not None

    async def test_start_services_enabled_creates_browser(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test start_services with discovery enabled creates browser."""
        with patch(
            "custom_components.esp_weaver.iot.client.device_api.zeroconf.async_get_instance"
        ) as mock_zeroconf:
            mock_zc = MagicMock()
            mock_zeroconf.return_value = mock_zc

            with patch(
                "custom_components.esp_weaver.iot.client.device_api.ServiceBrowser"
            ) as mock_browser_class:
                mock_browser = MagicMock()
                mock_browser_class.return_value = mock_browser

                await api.start_services(enable_discovery=True)

                # Assert ServiceBrowser was instantiated with expected arguments
                mock_browser_class.assert_called_once()
                call_args = mock_browser_class.call_args
                # First arg should be the zeroconf instance
                assert call_args[0][0] is mock_zc
                # Second arg should be the service type (MDNS_SERVICE_TYPE)
                assert "_esp_local_ctrl._tcp.local." in call_args[0][1]
                # Third arg should be the listener
                assert call_args[0][2] is api._global_listener
                # Browser should be assigned to api._global_browser
                assert api._global_browser is mock_browser


class TestESPWeaverApiDeviceData:
    """Test ESPWeaverApi device data methods."""

    def test_get_device_data(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test get_device_data method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        result = api.get_device_data(TEST_NODE_ID)

        assert result is not None
        assert result["ip"] == TEST_IP_ADDRESS

    def test_get_device_data_not_found(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test get_device_data for non-existent device."""
        result = api.get_device_data("non_existent")

        assert result is None

    async def test_update_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test update_device method."""
        with patch.object(
            api,
            "register_device",
            new_callable=AsyncMock,
        ) as mock_register:
            await api.update_device(TEST_NODE_ID, TEST_IP_ADDRESS, port=TEST_PORT)

            # update_device passes port as positional argument
            mock_register.assert_called_once_with(
                TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT
            )


class TestESPWeaverApiDevices:
    """Test ESPWeaverApi devices property."""

    def test_devices_property_empty(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test devices property returns empty dict."""
        assert api.devices == {}

    def test_devices_property_with_devices(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test devices property returns device dict."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        result = api.devices

        assert TEST_NODE_ID in result
        assert result[TEST_NODE_ID]["ip"] == TEST_IP_ADDRESS


class TestESPWeaverApiInternalMethods:
    """Test ESPWeaverApi internal methods."""

    async def test_connect_and_sync_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _connect_and_sync_device method."""
        with (
            patch.object(
                api._connection,
                "connect_and_sync",
                new_callable=AsyncMock,
                return_value=(True, [{"name": "config", "value": b"{}"}]),
            ),
            patch.object(
                api._discovery,
                "parse_and_discover_entities",
                new_callable=AsyncMock,
            ),
        ):
            success, properties = await api._connect_and_sync_device(
                TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT
            )

            assert success is True
            assert properties is not None

    async def test_connect_and_sync_device_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _connect_and_sync_device when connection fails."""
        with patch.object(
            api._connection,
            "connect_and_sync",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            success, properties = await api._connect_and_sync_device(
                TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT
            )

            assert success is False
            assert properties is None

    async def test_reconnect_and_retry_property(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _reconnect_and_retry_property method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)

        with (
            patch.object(
                api._connection,
                "connect_and_sync",
                new_callable=AsyncMock,
                return_value=(True, []),
            ),
            patch.object(
                api,
                "set_local_ctrl_property",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await api._reconnect_and_retry_property(
                TEST_NODE_ID, "power", True
            )

            assert result is True

    async def test_reconnect_and_retry_property_no_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _reconnect_and_retry_property with no device."""
        result = await api._reconnect_and_retry_property(TEST_NODE_ID, "power", True)

        assert result is False

    async def test_reconnect_and_retry_property_connect_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _reconnect_and_retry_property when connect fails."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)

        with patch.object(
            api._connection,
            "connect_and_sync",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = await api._reconnect_and_retry_property(
                TEST_NODE_ID, "power", True
            )

            assert result is False
