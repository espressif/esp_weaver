# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the network discovery module."""

import asyncio
from collections.abc import Generator
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.discovery.network import (
    DISCOVERY_SETTLE_TIME,
    DISCOVERY_THROTTLE_INTERVAL,
    DISCOVERY_TIMEOUT,
    MDNS_SERVICE_TYPE,
    BaseMDNSListener,
    ESPDeviceListener,
    GlobalMDNSListener,
    _extract_node_id_from_service,
    _format_node_id,
    _is_valid_node_id,
    async_discover_devices,
    extract_ip_from_service_info,
)
from custom_components.esp_weaver.iot.specs.device_specs import DEFAULT_PORT

# Module-level fixtures for mock Home Assistant instances


@pytest.fixture
def mock_hass() -> Generator[MagicMock]:
    """Create mock Home Assistant instance."""
    hass = MagicMock()
    loop = asyncio.new_event_loop()
    hass.loop = loop

    def _mock_async_create_task(coro):
        """Mock async_create_task that properly closes the coroutine."""
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    hass.async_create_task = _mock_async_create_task
    yield hass
    loop.close()


@pytest.fixture
def mock_hass_with_threadsafe(mock_hass: MagicMock) -> MagicMock:
    """Create mock Home Assistant instance with call_soon_threadsafe support."""

    def _mock_call_soon_threadsafe(callback, *args):
        """Mock call_soon_threadsafe that invokes callback to close coroutines."""
        callback(*args)

    mock_hass.loop.call_soon_threadsafe = MagicMock(
        side_effect=_mock_call_soon_threadsafe
    )
    return mock_hass


@pytest.fixture
def mock_hass_with_config_entries(mock_hass: MagicMock) -> MagicMock:
    """Create mock Home Assistant instance with config_entries support."""
    mock_hass.config_entries = MagicMock()
    return mock_hass


class TestExtractIpFromServiceInfo:
    """Test extract_ip_from_service_info function."""

    def test_valid_service_info(self) -> None:
        """Test extracting IP from valid service info."""
        mock_info = MagicMock()
        mock_info.addresses = [socket.inet_aton("192.168.1.100")]

        result = extract_ip_from_service_info(mock_info)

        assert result == "192.168.1.100"

    def test_multiple_addresses(self) -> None:
        """Test extracting first IP when multiple addresses."""
        mock_info = MagicMock()
        mock_info.addresses = [
            socket.inet_aton("192.168.1.100"),
            socket.inet_aton("192.168.1.101"),
        ]

        result = extract_ip_from_service_info(mock_info)

        assert result == "192.168.1.100"

    def test_no_addresses(self) -> None:
        """Test handling empty addresses list."""
        mock_info = MagicMock()
        mock_info.addresses = []

        result = extract_ip_from_service_info(mock_info)

        assert result is None

    def test_none_service_info(self) -> None:
        """Test handling None service info."""
        result = extract_ip_from_service_info(None)

        assert result is None


class TestIsValidNodeId:
    """Test _is_valid_node_id function."""

    def test_valid_alphanumeric(self) -> None:
        """Test valid alphanumeric node ID."""
        assert _is_valid_node_id("ABC123") is True

    def test_valid_with_underscore(self) -> None:
        """Test valid node ID with underscore."""
        assert _is_valid_node_id("node_123") is True

    def test_valid_with_hyphen(self) -> None:
        """Test valid node ID with hyphen."""
        assert _is_valid_node_id("node-123") is True

    def test_valid_mixed(self) -> None:
        """Test valid node ID with mixed characters."""
        assert _is_valid_node_id("ESP32_Node-1") is True

    def test_empty_string(self) -> None:
        """Test empty string is invalid."""
        assert _is_valid_node_id("") is False

    def test_too_long(self) -> None:
        """Test node ID over 64 characters is invalid."""
        assert _is_valid_node_id("a" * 65) is False

    def test_exactly_64_chars(self) -> None:
        """Test node ID exactly 64 characters is valid."""
        assert _is_valid_node_id("a" * 64) is True

    def test_invalid_characters(self) -> None:
        """Test node ID with invalid characters is invalid."""
        assert _is_valid_node_id("node.123") is False
        assert _is_valid_node_id("node@123") is False
        assert _is_valid_node_id("node 123") is False


class TestFormatNodeId:
    """Test _format_node_id function."""

    def test_already_valid(self) -> None:
        """Test already valid node ID unchanged."""
        assert _format_node_id("ABC123") == "ABC123"

    def test_spaces_replaced(self) -> None:
        """Test spaces replaced with underscores."""
        assert _format_node_id("node 123") == "node_123"

    def test_dots_replaced(self) -> None:
        """Test dots replaced with underscores."""
        assert _format_node_id("node.123") == "node_123"

    def test_special_chars_replaced(self) -> None:
        """Test special characters replaced."""
        assert _format_node_id("node@123!") == "node_123_"


class TestExtractNodeIdFromService:
    """Test _extract_node_id_from_service function."""

    def test_valid_node_id_in_properties(self) -> None:
        """Test extracting node_id from service properties."""
        mock_info = MagicMock()
        mock_info.properties = {
            b"node_id": b"ESP_NODE_123",
            b"device_name": b"My Device",
        }

        node_id, device_name = _extract_node_id_from_service(mock_info)

        assert node_id == "ESP_NODE_123"
        assert device_name == "My Device"

    def test_no_properties(self) -> None:
        """Test handling service info without properties."""
        mock_info = MagicMock()
        mock_info.properties = None

        node_id, device_name = _extract_node_id_from_service(mock_info)

        assert node_id is None
        assert device_name is None

    def test_empty_properties(self) -> None:
        """Test handling empty properties."""
        mock_info = MagicMock()
        mock_info.properties = {}

        node_id, device_name = _extract_node_id_from_service(mock_info)

        assert node_id is None
        assert device_name is None

    def test_none_service_info(self) -> None:
        """Test handling None service info."""
        node_id, device_name = _extract_node_id_from_service(None)

        assert node_id is None
        assert device_name is None

    def test_device_name_only(self) -> None:
        """Test extracting device_name when no node_id."""
        mock_info = MagicMock()
        mock_info.properties = {
            b"device_name": b"My Device",
        }

        node_id, device_name = _extract_node_id_from_service(mock_info)

        assert node_id is None
        assert device_name == "My Device"

    def test_invalid_node_id_format(self) -> None:
        """Test handling invalid node_id format."""
        mock_info = MagicMock()
        mock_info.properties = {
            b"node_id": b"invalid.node.id@with!special",
        }

        node_id, _device_name = _extract_node_id_from_service(mock_info)

        # Invalid format should be rejected
        assert node_id is None


class TestESPDeviceListener:
    """Test ESPDeviceListener class."""

    def test_init(self, mock_hass: MagicMock) -> None:
        """Test listener initialization."""
        listener = ESPDeviceListener(mock_hass)

        assert listener.hass == mock_hass
        assert listener.devices == []
        assert listener.seen_node_ids == set()
        assert isinstance(listener.discovered_event, asyncio.Event)

    def test_reset(self, mock_hass: MagicMock) -> None:
        """Test listener reset."""
        listener = ESPDeviceListener(mock_hass)
        listener.devices = [{"node_id": "test"}]
        listener.seen_node_ids = {"test"}
        listener.discovered_event.set()
        listener._last_discovery_times = {"test": 1234567890.0}

        listener.reset()

        assert listener.devices == []
        assert listener.seen_node_ids == set()
        assert not listener.discovered_event.is_set()
        assert listener._last_discovery_times == {}

    async def test_handle_discovery_new_device(self, mock_hass: MagicMock) -> None:
        """Test handling new device discovery."""
        listener = ESPDeviceListener(mock_hass)

        await listener._async_handle_discovery(
            "node123", "192.168.1.100", 8080, "My Device"
        )

        assert len(listener.devices) == 1
        assert listener.devices[0]["node_id"] == "node123"
        assert listener.devices[0]["ip"] == "192.168.1.100"
        assert listener.devices[0]["port"] == 8080
        assert listener.devices[0]["device_name"] == "My Device"
        assert "node123" in listener.seen_node_ids
        assert listener.discovered_event.is_set()

    async def test_handle_discovery_duplicate_device(
        self, mock_hass: MagicMock
    ) -> None:
        """Test duplicate device discovery is ignored."""
        listener = ESPDeviceListener(mock_hass)
        listener.seen_node_ids.add("node123")
        listener._last_discovery_times = {}

        await listener._async_handle_discovery("node123", "192.168.1.100", 8080, None)

        assert len(listener.devices) == 0

    async def test_handle_discovery_default_port(self, mock_hass: MagicMock) -> None:
        """Test default port when not specified."""
        listener = ESPDeviceListener(mock_hass)

        # Call without port argument to use default
        await listener._async_handle_discovery("node123", "192.168.1.100")

        assert listener.devices[0]["port"] == DEFAULT_PORT

    async def test_handle_removal(self, mock_hass: MagicMock) -> None:
        """Test device removal."""
        listener = ESPDeviceListener(mock_hass)
        listener.devices = [{"node_id": "node123", "ip": "192.168.1.100"}]
        listener.seen_node_ids = {"node123"}

        await listener._async_handle_removal("node123")

        assert len(listener.devices) == 0
        assert "node123" not in listener.seen_node_ids

    async def test_handle_removal_unknown_device(self, mock_hass: MagicMock) -> None:
        """Test removal of unknown device."""
        listener = ESPDeviceListener(mock_hass)

        # Should not raise
        await listener._async_handle_removal("unknown")

        assert len(listener.devices) == 0


class TestGlobalMDNSListener:
    """Test GlobalMDNSListener class."""

    @pytest.fixture
    def mock_api(self) -> MagicMock:
        """Create mock API instance."""
        api = MagicMock()
        api.domain = "esp_weaver"
        api.update_device = AsyncMock()
        return api

    def test_init(
        self, mock_hass_with_config_entries: MagicMock, mock_api: MagicMock
    ) -> None:
        """Test global listener initialization."""
        listener = GlobalMDNSListener(mock_hass_with_config_entries, api=mock_api)

        assert listener.hass == mock_hass_with_config_entries
        assert listener.api == mock_api

    async def test_process_service_removed_ignored(
        self, mock_hass_with_config_entries: MagicMock, mock_api: MagicMock
    ) -> None:
        """Test removed change type is ignored."""
        listener = GlobalMDNSListener(mock_hass_with_config_entries, api=mock_api)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "removed", "service_name"
        )

        mock_api.update_device.assert_not_called()

    async def test_process_service_unknown_device(
        self, mock_hass_with_config_entries: MagicMock, mock_api: MagicMock
    ) -> None:
        """Test unknown device is ignored."""
        mock_entry = MagicMock()
        mock_entry.unique_id = "other_node"
        mock_hass_with_config_entries.config_entries.async_entries.return_value = [
            mock_entry
        ]
        listener = GlobalMDNSListener(mock_hass_with_config_entries, api=mock_api)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "added", "service_name"
        )

        mock_api.update_device.assert_not_called()

    async def test_process_service_known_device(
        self, mock_hass_with_config_entries: MagicMock, mock_api: MagicMock
    ) -> None:
        """Test known device triggers update when not connected."""
        mock_entry = MagicMock()
        mock_entry.unique_id = "node123"
        mock_hass_with_config_entries.config_entries.async_entries.return_value = [
            mock_entry
        ]
        # Device is not connected (no active client)
        mock_api.registry.get_client.return_value = None
        listener = GlobalMDNSListener(mock_hass_with_config_entries, api=mock_api)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "added", "service_name"
        )

        mock_api.update_device.assert_called_once_with("node123", "192.168.1.100", 8080)

    async def test_process_service_connected_device_ip_change(
        self, mock_hass_with_config_entries: MagicMock, mock_api: MagicMock
    ) -> None:
        """Test connected device updates IP/port without full reconnect."""
        mock_entry = MagicMock()
        mock_entry.unique_id = "node123"
        mock_hass_with_config_entries.config_entries.async_entries.return_value = [
            mock_entry
        ]
        # Device is connected (has active client)
        mock_api.registry.get_client.return_value = MagicMock()
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.50"
        mock_device.port = 8080
        mock_api.registry.get_device.return_value = mock_device
        listener = GlobalMDNSListener(mock_hass_with_config_entries, api=mock_api)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "added", "service_name"
        )

        # Should NOT call update_device (no reconnection)
        mock_api.update_device.assert_not_called()
        # Should update device IP directly
        assert mock_device.ip == "192.168.1.100"
        assert mock_device.port == 8080

    async def test_process_service_connected_device_same_ip(
        self, mock_hass_with_config_entries: MagicMock, mock_api: MagicMock
    ) -> None:
        """Test connected device with same IP skips update entirely."""
        mock_entry = MagicMock()
        mock_entry.unique_id = "node123"
        mock_hass_with_config_entries.config_entries.async_entries.return_value = [
            mock_entry
        ]
        # Device is connected (has active client)
        mock_api.registry.get_client.return_value = MagicMock()
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.100"
        mock_device.port = 8080
        mock_api.registry.get_device.return_value = mock_device
        listener = GlobalMDNSListener(mock_hass_with_config_entries, api=mock_api)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "added", "service_name"
        )

        # Should NOT call update_device (no change needed)
        mock_api.update_device.assert_not_called()


class TestBaseMDNSListener:
    """Test BaseMDNSListener class."""

    def test_add_service_schedules_handling(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test add_service schedules async handling."""
        listener = BaseMDNSListener(mock_hass_with_threadsafe)
        mock_zc = MagicMock()

        listener.add_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")

        mock_hass_with_threadsafe.loop.call_soon_threadsafe.assert_called_once()

    def test_update_service_schedules_handling(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test update_service schedules async handling."""
        listener = BaseMDNSListener(mock_hass_with_threadsafe)
        mock_zc = MagicMock()

        listener.update_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")

        mock_hass_with_threadsafe.loop.call_soon_threadsafe.assert_called_once()

    def test_remove_service_default(self, mock_hass_with_threadsafe: MagicMock) -> None:
        """Test remove_service default implementation does nothing."""
        listener = BaseMDNSListener(mock_hass_with_threadsafe)
        mock_zc = MagicMock()

        # Should not raise
        listener.remove_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")


class TestMDNSServiceType:
    """Test mDNS service type constant."""

    def test_service_type_format(self) -> None:
        """Test service type has correct format."""
        assert MDNS_SERVICE_TYPE == "_esp_local_ctrl._tcp.local."


class TestDiscoveryConstants:
    """Test discovery constants."""

    def test_timeout_values(self) -> None:
        """Test timeout values are reasonable."""
        assert DISCOVERY_TIMEOUT > 0
        assert DISCOVERY_SETTLE_TIME > 0
        assert DISCOVERY_THROTTLE_INTERVAL > 0
        assert DISCOVERY_TIMEOUT > DISCOVERY_SETTLE_TIME


class TestAsyncDiscoverDevices:
    """Test async_discover_devices function."""

    async def test_discover_devices_success(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test successful device discovery."""
        mock_listener = MagicMock()
        mock_listener.discovered_event = asyncio.Event()
        mock_listener.devices = [{"node_id": "test_node", "ip": "192.168.1.100"}]
        mock_listener.reset = MagicMock()

        mock_listener_class = MagicMock(return_value=mock_listener)

        mock_browser = MagicMock()
        mock_browser.cancel = MagicMock()

        # Set the event immediately to avoid timeout
        mock_listener.discovered_event.set()

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ServiceBrowser",
                return_value=mock_browser,
            ),
        ):
            mock_get_zc.return_value = MagicMock()

            result = await async_discover_devices(
                mock_hass_with_threadsafe, mock_listener_class
            )

        assert result == [{"node_id": "test_node", "ip": "192.168.1.100"}]
        mock_listener.reset.assert_called()

    async def test_discover_devices_timeout(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test discovery timeout returns empty list."""
        mock_listener = MagicMock()
        mock_listener.discovered_event = asyncio.Event()  # Never set
        mock_listener.devices = []
        mock_listener.reset = MagicMock()

        mock_listener_class = MagicMock(return_value=mock_listener)

        mock_browser = MagicMock()
        mock_browser.cancel = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ServiceBrowser",
                return_value=mock_browser,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.DISCOVERY_TIMEOUT",
                0.01,  # Very short timeout for test
            ),
        ):
            mock_get_zc.return_value = MagicMock()

            result = await async_discover_devices(
                mock_hass_with_threadsafe, mock_listener_class
            )

        assert result == []
        mock_browser.cancel.assert_called()

    async def test_discover_devices_os_error(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test OSError during discovery returns empty list."""
        mock_listener_class = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
            new_callable=AsyncMock,
            side_effect=OSError("Network error"),
        ):
            result = await async_discover_devices(
                mock_hass_with_threadsafe, mock_listener_class
            )

        assert result == []

    async def test_discover_devices_unexpected_error(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test unexpected exception during discovery is re-raised.

        Only OSError is caught and returns empty list. Other exceptions
        are propagated to the caller.
        """
        mock_listener_class = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Unexpected error"),
            ),
            pytest.raises(RuntimeError, match="Unexpected error"),
        ):
            await async_discover_devices(mock_hass_with_threadsafe, mock_listener_class)

    async def test_discover_devices_browser_without_cancel(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test handling browser without cancel method."""
        mock_listener = MagicMock()
        mock_listener.discovered_event = asyncio.Event()
        mock_listener.devices = []
        mock_listener.reset = MagicMock()

        mock_listener_class = MagicMock(return_value=mock_listener)

        # Browser without cancel attribute
        mock_browser = MagicMock(spec=[])  # No methods

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ServiceBrowser",
                return_value=mock_browser,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.DISCOVERY_TIMEOUT",
                0.01,
            ),
        ):
            mock_get_zc.return_value = MagicMock()

            result = await async_discover_devices(
                mock_hass_with_threadsafe, mock_listener_class
            )

        assert result == []


class TestBaseMDNSListenerAsyncHandler:
    """Test BaseMDNSListener async handler methods."""

    async def test_async_handle_service_change_success(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test successful service change handling."""
        listener = ESPDeviceListener(mock_hass_with_threadsafe)

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.AsyncServiceInfo"
            ) as mock_async_info,
        ):
            mock_zc = MagicMock()
            mock_get_zc.return_value = mock_zc

            mock_info_instance = MagicMock()
            mock_info_instance.addresses = [socket.inet_aton("192.168.1.100")]
            mock_info_instance.port = 8080
            mock_info_instance.properties = {b"node_id": b"test_node"}
            mock_info_instance.async_request = AsyncMock(return_value=True)
            mock_async_info.return_value = mock_info_instance

            await listener._async_handle_service_change(
                "_esp_local_ctrl._tcp.local.",
                "test_service",
                "added",
            )

        # Device should be added
        assert len(listener.devices) == 1
        assert listener.devices[0]["node_id"] == "test_node"

    async def test_async_handle_service_change_request_failed(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test handler when service info request fails."""
        listener = ESPDeviceListener(mock_hass_with_threadsafe)

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.AsyncServiceInfo"
            ) as mock_async_info,
        ):
            mock_zc = MagicMock()
            mock_get_zc.return_value = mock_zc

            mock_info_instance = MagicMock()
            mock_info_instance.async_request = AsyncMock(return_value=False)
            mock_async_info.return_value = mock_info_instance

            await listener._async_handle_service_change(
                "_esp_local_ctrl._tcp.local.",
                "test_service",
                "added",
            )

        # No device should be added
        assert len(listener.devices) == 0

    async def test_async_handle_service_change_no_addresses(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test handler when no addresses in service info."""
        listener = ESPDeviceListener(mock_hass_with_threadsafe)

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.AsyncServiceInfo"
            ) as mock_async_info,
        ):
            mock_zc = MagicMock()
            mock_get_zc.return_value = mock_zc

            mock_info_instance = MagicMock()
            mock_info_instance.addresses = []  # No addresses
            mock_info_instance.async_request = AsyncMock(return_value=True)
            mock_async_info.return_value = mock_info_instance

            await listener._async_handle_service_change(
                "_esp_local_ctrl._tcp.local.",
                "test_service",
                "added",
            )

        # No device should be added
        assert len(listener.devices) == 0

    async def test_async_handle_service_change_no_node_id(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test handler when node_id extraction fails."""
        listener = ESPDeviceListener(mock_hass_with_threadsafe)

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.discovery.network.AsyncServiceInfo"
            ) as mock_async_info,
        ):
            mock_zc = MagicMock()
            mock_get_zc.return_value = mock_zc

            mock_info_instance = MagicMock()
            mock_info_instance.addresses = [socket.inet_aton("192.168.1.100")]
            mock_info_instance.port = 8080
            mock_info_instance.properties = {}  # No node_id
            mock_info_instance.async_request = AsyncMock(return_value=True)
            mock_async_info.return_value = mock_info_instance

            await listener._async_handle_service_change(
                "_esp_local_ctrl._tcp.local.",
                "test_service",
                "added",
            )

        # No device should be added
        assert len(listener.devices) == 0

    async def test_async_handle_service_change_exception(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test handler catches exceptions."""
        listener = ESPDeviceListener(mock_hass_with_threadsafe)

        with patch(
            "custom_components.esp_weaver.iot.discovery.network.zeroconf.async_get_instance",
            new_callable=AsyncMock,
            side_effect=ValueError("Test error"),
        ):
            # Should not raise - use proper service name format
            await listener._async_handle_service_change(
                "_esp_local_ctrl._tcp.local.",
                "TEST_NODE._esp_local_ctrl._tcp.local.",
                "added",
            )

        # No device should be added
        assert len(listener.devices) == 0


class TestESPDeviceListenerRemoveService:
    """Test ESPDeviceListener.remove_service method."""

    def test_remove_service_schedules_handling(
        self, mock_hass_with_threadsafe: MagicMock
    ) -> None:
        """Test remove_service schedules async handling."""
        listener = ESPDeviceListener(mock_hass_with_threadsafe)
        mock_zc = MagicMock()

        listener.remove_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")

        # Should schedule async handling
        mock_hass_with_threadsafe.loop.call_soon_threadsafe.assert_called_once()


class TestESPDeviceListenerProcessService:
    """Test ESPDeviceListener._async_process_service method."""

    async def test_process_service_added(self, mock_hass: MagicMock) -> None:
        """Test processing added service."""
        listener = ESPDeviceListener(mock_hass)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "added", "service_name"
        )

        assert len(listener.devices) == 1
        assert listener.devices[0]["node_id"] == "node123"

    async def test_process_service_removed(self, mock_hass: MagicMock) -> None:
        """Test processing removed service."""
        listener = ESPDeviceListener(mock_hass)
        listener.devices = [{"node_id": "node123", "ip": "192.168.1.100"}]
        listener.seen_node_ids = {"node123"}

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "removed", "service_name"
        )

        assert len(listener.devices) == 0
        assert "node123" not in listener.seen_node_ids

    async def test_process_service_updated(self, mock_hass: MagicMock) -> None:
        """Test processing updated service."""
        listener = ESPDeviceListener(mock_hass)

        await listener._async_process_service(
            "node123", "192.168.1.100", 8080, "Device", "updated", "service_name"
        )

        # Updated is treated like added for new devices
        assert len(listener.devices) == 1


class TestESPDeviceListenerThrottling:
    """Test ESPDeviceListener throttling behavior."""

    async def test_throttle_rapid_discoveries(self, mock_hass: MagicMock) -> None:
        """Test throttling of rapid discoveries for same node."""
        listener = ESPDeviceListener(mock_hass)
        listener.seen_node_ids.add("node123")

        # Mock time.time() to return a value earlier than _last_discovery_times
        # This simulates the last discovery being in the future relative to "now"
        with patch("time.time", return_value=1000.0):
            listener._last_discovery_times["node123"] = (
                2000.0  # Set last discovery to be in the future
            )

            await listener._async_handle_discovery(
                "node123", "192.168.1.100", 8080, None
            )

            # Should be throttled (node already seen and time not passed)
            assert len(listener.devices) == 0
