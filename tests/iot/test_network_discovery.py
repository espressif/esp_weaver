# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the network discovery module."""

import asyncio
from collections.abc import Coroutine
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.discovered_device import DiscoveredDevice
from custom_components.esp_weaver.iot.discovery.network import (
    DISCOVERY_SETTLE_TIME,
    DISCOVERY_THROTTLE_INTERVAL,
    DISCOVERY_TIMEOUT,
    MDNS_SERVICE_TYPE,
    BaseMDNSListener,
    ESPDeviceListener,
    GlobalMDNSListener,
    _extract_node_id_from_service,
    _is_valid_node_id,
    async_discover_devices,
    extract_model_from_service,
    ipv4_of,
)
from custom_components.esp_weaver.iot.specs.device_specs import DEFAULT_PORT
from tests.fakes import FakeHost


def _found(node_id: str, ip: str = "192.168.1.100", **fields: Any) -> DiscoveredDevice:
    """Return a device as discovery lists it."""
    return DiscoveredDevice(ip=ip, node_id=node_id, **fields)


@pytest.fixture
def mock_host() -> FakeHost:
    """A host whose loop runs scheduling callbacks inline and drops tasks."""
    host = FakeHost()
    host.loop = MagicMock()
    host.loop.call_soon_threadsafe = MagicMock(side_effect=lambda cb, *a: cb(*a))

    def _drop_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        coro.close()
        return MagicMock()

    host.create_task = _drop_task
    return host


class TestIpv4Of:
    """Test ipv4_of function."""

    def test_valid_service_info(self) -> None:
        """Test extracting IP from valid service info."""
        mock_info = MagicMock()
        mock_info.addresses = [socket.inet_aton("192.168.1.100")]

        result = ipv4_of(mock_info)

        assert result == "192.168.1.100"

    def test_multiple_addresses(self) -> None:
        """Test extracting first IP when multiple addresses."""
        mock_info = MagicMock()
        mock_info.addresses = [
            socket.inet_aton("192.168.1.100"),
            socket.inet_aton("192.168.1.101"),
        ]

        result = ipv4_of(mock_info)

        assert result == "192.168.1.100"

    def test_an_ipv6_address_listed_first_is_skipped(self) -> None:
        """Test a dual-stack node that advertises IPv6 ahead of IPv4."""
        mock_info = MagicMock()
        mock_info.addresses = [
            socket.inet_pton(socket.AF_INET6, "fe80::1"),
            socket.inet_aton("192.168.1.100"),
        ]

        assert ipv4_of(mock_info) == "192.168.1.100"

    def test_no_addresses(self) -> None:
        """Test handling empty addresses list."""
        mock_info = MagicMock()
        mock_info.addresses = []

        result = ipv4_of(mock_info)

        assert result is None

    def test_none_service_info(self) -> None:
        """Test handling None service info."""
        result = ipv4_of(None)

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

    def test_a_valid_node_id_is_taken_as_advertised(self) -> None:
        """Underscores and hyphens are the id's own; nothing is rewritten."""
        mock_info = MagicMock()
        mock_info.properties = {b"node_id": b"ESP32_Node-1"}

        node_id, _device_name = _extract_node_id_from_service(mock_info)

        assert node_id == "ESP32_Node-1"

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


class TestExtractIdentityFromService:
    """``model`` and ``types`` come through as sent; absence is told from empty."""

    def test_the_model_comes_through_as_text(self) -> None:
        """The ``model`` record as sent; the node ``type`` record is not read."""
        mock_info = MagicMock()
        mock_info.properties = {
            b"node_id": b"ESP_NODE_123",
            b"model": b"led_light",
            b"type": b"Lightbulb",
        }

        assert extract_model_from_service(mock_info) == "led_light"

    def test_a_record_the_firmware_left_out_is_none(self) -> None:
        """Older firmware advertises no model; that is not an empty model."""
        mock_info = MagicMock()
        mock_info.properties = {b"node_id": b"ESP_NODE_123"}

        assert extract_model_from_service(mock_info) is None

    def test_an_empty_value_is_none(self) -> None:
        """A key with nothing after it says nothing either."""
        mock_info = MagicMock()
        mock_info.properties = {b"model": b""}

        assert extract_model_from_service(mock_info) is None

    def test_nothing_to_read_from(self) -> None:
        """No service, or one without properties, says nothing."""
        mock_info = MagicMock()
        mock_info.properties = None

        assert extract_model_from_service(None) is None
        assert extract_model_from_service(mock_info) is None


class TestESPDeviceListener:
    """Test ESPDeviceListener class."""

    def test_init(self, mock_host: FakeHost) -> None:
        """Test listener initialization."""
        listener = ESPDeviceListener(mock_host)

        assert listener.host is mock_host
        assert listener.devices == []
        assert isinstance(listener.discovered_event, asyncio.Event)

    async def test_handle_discovery_new_device(self, mock_host: FakeHost) -> None:
        """Test handling new device discovery."""
        listener = ESPDeviceListener(mock_host)

        await listener._async_handle_discovery(
            _found("node123", port=8080, device_name="My Device")
        )

        assert listener.devices == [
            _found("node123", port=8080, device_name="My Device")
        ]
        assert listener.discovered_event.is_set()

    async def test_handle_discovery_duplicate_device(self, mock_host: FakeHost) -> None:
        """A device announcing itself again is brought up to date, not listed twice."""
        listener = ESPDeviceListener(mock_host)
        await listener._async_handle_discovery(
            _found("node123", port=8080, device_name="My Device")
        )
        listener._last_discovery_times.clear()

        await listener._async_handle_discovery(
            _found("node123", ip="192.168.1.101", port=8080)
        )

        assert len(listener.devices) == 1
        assert listener.devices[0].ip == "192.168.1.101"
        assert listener.devices[0].device_name is None

    async def test_handle_discovery_default_port(self, mock_host: FakeHost) -> None:
        """Test default port when not specified."""
        listener = ESPDeviceListener(mock_host)

        await listener._async_handle_discovery(_found("node123"))

        assert listener.devices[0].port == DEFAULT_PORT

    async def test_handle_removal(self, mock_host: FakeHost) -> None:
        """Test device removal."""
        listener = ESPDeviceListener(mock_host)
        listener.devices = [_found("node123")]

        await listener._async_handle_removal("node123")

        assert listener.devices == []

    async def test_handle_removal_unknown_device(self, mock_host: FakeHost) -> None:
        """Test removal of unknown device."""
        listener = ESPDeviceListener(mock_host)

        # Should not raise
        await listener._async_handle_removal("unknown")

        assert len(listener.devices) == 0


class TestGlobalMDNSListener:
    """Test GlobalMDNSListener class."""

    @pytest.fixture
    def mock_api(self) -> MagicMock:
        """Create mock API instance."""
        api = MagicMock()
        api.register_device = AsyncMock()
        return api

    def test_init(self, mock_host: FakeHost, mock_api: MagicMock) -> None:
        """Test global listener initialization."""
        listener = GlobalMDNSListener(mock_host, api=mock_api)

        assert listener.host is mock_host
        assert listener.api == mock_api

    def test_a_removal_is_not_acted_on(
        self, mock_host: FakeHost, mock_api: MagicMock
    ) -> None:
        """A device going away is nothing to reconnect to; nothing is scheduled."""
        listener = GlobalMDNSListener(mock_host, api=mock_api)

        listener.remove_service(MagicMock(), MDNS_SERVICE_TYPE, "service_name")

        mock_host.loop.call_soon_threadsafe.assert_not_called()
        mock_api.register_device.assert_not_called()

    async def test_process_service_unknown_device(
        self, mock_host: FakeHost, mock_api: MagicMock
    ) -> None:
        """Test unknown device is ignored."""
        mock_host.configured = {"other_node"}
        listener = GlobalMDNSListener(mock_host, api=mock_api)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "added"
        )

        mock_api.register_device.assert_not_called()

    async def test_process_service_known_device(
        self, mock_host: FakeHost, mock_api: MagicMock
    ) -> None:
        """Test known device triggers update when not connected."""
        mock_host.configured = {"node123"}
        # Device is not connected (no active client)
        mock_api.registry.get_client.return_value = None
        mock_api.is_cloud_device.return_value = False
        listener = GlobalMDNSListener(mock_host, api=mock_api)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "added"
        )

        mock_api.register_device.assert_called_once_with(
            "node123", "192.168.1.100", 8080
        )
        # The host is told where the node is, for the entry to follow.
        assert mock_host.addresses == [("node123", "192.168.1.100", 8080)]

    async def test_process_service_connected_device_ip_change(
        self, mock_host: FakeHost, mock_api: MagicMock
    ) -> None:
        """Test connected device updates IP/port without full reconnect."""
        mock_host.configured = {"node123"}
        # Device is connected (has active client)
        mock_api.registry.get_client.return_value = MagicMock()
        mock_api.is_cloud_device.return_value = False
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.50"
        mock_device.port = 8080
        mock_api.registry.get_device.return_value = mock_device
        listener = GlobalMDNSListener(mock_host, api=mock_api)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "added"
        )

        # Should NOT call register_device (no reconnection)
        mock_api.register_device.assert_not_called()
        # Only the registry's record of the address moves
        mock_api.registry.register_device.assert_called_once_with(
            "node123", "192.168.1.100", 8080
        )
        # And the host's record of the address, for the next start.
        assert mock_host.addresses == [("node123", "192.168.1.100", 8080)]

    async def test_process_service_connected_device_same_ip(
        self, mock_host: FakeHost, mock_api: MagicMock
    ) -> None:
        """Test connected device with same IP skips update entirely."""
        mock_host.configured = {"node123"}
        # Device is connected (has active client)
        mock_api.registry.get_client.return_value = MagicMock()
        mock_api.is_cloud_device.return_value = False
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.100"
        mock_device.port = 8080
        mock_api.registry.get_device.return_value = mock_device
        listener = GlobalMDNSListener(mock_host, api=mock_api)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "added"
        )

        # Should NOT call register_device (no change needed)
        mock_api.register_device.assert_not_called()
        # The host hears every announcement and decides itself whether it moved.
        assert mock_host.addresses == [("node123", "192.168.1.100", 8080)]


class TestBaseMDNSListener:
    """Test BaseMDNSListener class."""

    def test_add_service_schedules_handling(self, mock_host: FakeHost) -> None:
        """Test add_service schedules async handling."""
        listener = BaseMDNSListener(mock_host)
        mock_zc = MagicMock()

        listener.add_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")

        mock_host.loop.call_soon_threadsafe.assert_called_once()

    def test_update_service_schedules_handling(self, mock_host: FakeHost) -> None:
        """Test update_service schedules async handling."""
        listener = BaseMDNSListener(mock_host)
        mock_zc = MagicMock()

        listener.update_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")

        mock_host.loop.call_soon_threadsafe.assert_called_once()

    def test_remove_service_default(self, mock_host: FakeHost) -> None:
        """Test remove_service default implementation does nothing."""
        listener = BaseMDNSListener(mock_host)
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

    @staticmethod
    def _listener(devices: list[DiscoveredDevice], *, found: bool) -> MagicMock:
        """Return a stand-in for the listener the browser would feed."""
        listener = MagicMock()
        listener.discovered_event = asyncio.Event()
        listener.devices = devices
        if found:
            listener.discovered_event.set()
        return listener

    async def test_discover_devices_success(self, mock_host: FakeHost) -> None:
        """What the listener collected is what discovery answers, as a copy."""
        mock_listener = self._listener([_found("test_node")], found=True)
        mock_browser = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ESPDeviceListener",
                return_value=mock_listener,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ServiceBrowser",
                return_value=mock_browser,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.DISCOVERY_SETTLE_TIME",
                0,
            ),
        ):
            result = await async_discover_devices(mock_host)

        assert result == [_found("test_node")]
        assert result is not mock_listener.devices
        mock_browser.cancel.assert_called_once()
        mock_listener.cancel_pending.assert_called_once()

    async def test_discover_devices_timeout(self, mock_host: FakeHost) -> None:
        """Test discovery timeout returns empty list."""
        mock_listener = self._listener([], found=False)
        mock_browser = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ESPDeviceListener",
                return_value=mock_listener,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ServiceBrowser",
                return_value=mock_browser,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.DISCOVERY_TIMEOUT",
                0.01,  # Very short timeout for test
            ),
        ):
            result = await async_discover_devices(mock_host)

        assert result == []
        mock_browser.cancel.assert_called()

    async def test_discover_devices_os_error(self, mock_host: FakeHost) -> None:
        """Test OSError during discovery returns empty list."""
        with patch.object(
            mock_host,
            "zeroconf",
            new_callable=AsyncMock,
            side_effect=OSError("Network error"),
        ):
            result = await async_discover_devices(mock_host)

        assert result == []

    async def test_discover_devices_unexpected_error(self, mock_host: FakeHost) -> None:
        """Test unexpected exception during discovery is re-raised.

        Only OSError is caught and returns empty list. Other exceptions
        are propagated to the caller.
        """
        with (
            patch.object(
                mock_host,
                "zeroconf",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Unexpected error"),
            ),
            pytest.raises(RuntimeError, match="Unexpected error"),
        ):
            await async_discover_devices(mock_host)

    async def test_discover_devices_browser_cancel_failing(
        self, mock_host: FakeHost
    ) -> None:
        """Test a browser that fails to cancel does not lose the result."""
        mock_listener = self._listener([_found("test_node")], found=False)
        mock_browser = MagicMock()
        mock_browser.cancel = MagicMock(side_effect=OSError("already closed"))

        with (
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ESPDeviceListener",
                return_value=mock_listener,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.ServiceBrowser",
                return_value=mock_browser,
            ),
            patch(
                "custom_components.esp_weaver.iot.discovery.network.DISCOVERY_TIMEOUT",
                0.01,
            ),
        ):
            result = await async_discover_devices(mock_host)

        assert result == [_found("test_node")]
        mock_listener.cancel_pending.assert_called_once()


class TestBaseMDNSListenerAsyncHandler:
    """Test BaseMDNSListener async handler methods."""

    async def test_async_handle_service_change_success(
        self, mock_host: FakeHost
    ) -> None:
        """Test successful service change handling."""
        listener = ESPDeviceListener(mock_host)

        with (
            patch.object(
                mock_host,
                "zeroconf",
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
        assert listener.devices[0].node_id == "test_node"

    async def test_async_handle_service_change_request_failed(
        self, mock_host: FakeHost
    ) -> None:
        """Test handler when service info request fails."""
        listener = ESPDeviceListener(mock_host)

        with (
            patch.object(
                mock_host,
                "zeroconf",
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
        self, mock_host: FakeHost
    ) -> None:
        """Test handler when no addresses in service info."""
        listener = ESPDeviceListener(mock_host)

        with (
            patch.object(
                mock_host,
                "zeroconf",
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
        self, mock_host: FakeHost
    ) -> None:
        """Test handler when node_id extraction fails."""
        listener = ESPDeviceListener(mock_host)

        with (
            patch.object(
                mock_host,
                "zeroconf",
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
        self, mock_host: FakeHost
    ) -> None:
        """Test handler catches exceptions."""
        listener = ESPDeviceListener(mock_host)

        with patch.object(
            mock_host,
            "zeroconf",
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

    def test_remove_service_schedules_handling(self, mock_host: FakeHost) -> None:
        """Test remove_service schedules async handling."""
        listener = ESPDeviceListener(mock_host)
        mock_zc = MagicMock()

        listener.remove_service(mock_zc, "_esp_local_ctrl._tcp.local.", "test_service")

        # Should schedule async handling
        mock_host.loop.call_soon_threadsafe.assert_called_once()


class TestESPDeviceListenerProcessService:
    """Test ESPDeviceListener._async_process_service method."""

    async def test_process_service_added(self, mock_host: FakeHost) -> None:
        """Test processing added service."""
        listener = ESPDeviceListener(mock_host)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "added"
        )

        assert len(listener.devices) == 1
        assert listener.devices[0].node_id == "node123"

    async def test_process_service_removed(self, mock_host: FakeHost) -> None:
        """Test processing removed service."""
        listener = ESPDeviceListener(mock_host)
        listener.devices = [_found("node123")]

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "removed"
        )

        assert listener.devices == []

    async def test_process_service_updated(self, mock_host: FakeHost) -> None:
        """Test processing updated service."""
        listener = ESPDeviceListener(mock_host)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "updated"
        )

        # Updated is treated like added for new devices
        assert len(listener.devices) == 1

    async def test_what_the_device_advertised_is_kept_with_it(
        self, mock_host: FakeHost
    ) -> None:
        """The model rides along, and follows a re-announcement."""
        listener = ESPDeviceListener(mock_host)

        await listener._async_process_service(
            _found(
                "node123",
                port=8080,
                device_name="Device",
                model="led_light",
            ),
            "added",
        )

        assert listener.devices[0].model == "led_light"

        # A later announcement is the device's current word, throttling aside.
        listener._last_discovery_times.clear()
        await listener._async_process_service(
            _found(
                "node123",
                ip="192.168.1.101",
                port=8080,
                device_name="Device",
                model="A2",
            ),
            "updated",
        )

        assert len(listener.devices) == 1
        assert listener.devices[0].ip == "192.168.1.101"
        assert listener.devices[0].model == "A2"

    async def test_a_device_that_advertised_nothing_is_still_listed(
        self, mock_host: FakeHost
    ) -> None:
        """Older firmware is found; whether to offer it is the flow's call."""
        listener = ESPDeviceListener(mock_host)

        await listener._async_process_service(
            _found("node123", port=8080, device_name="Device"), "added"
        )

        assert listener.devices[0].model is None


class TestESPDeviceListenerThrottling:
    """Test ESPDeviceListener throttling behavior."""

    async def test_throttle_rapid_discoveries(self, mock_host: FakeHost) -> None:
        """Test throttling of rapid discoveries for same node."""
        listener = ESPDeviceListener(mock_host)
        listener.devices = [_found("node123", port=8080, device_name="Device")]

        # Mock time.time() to return a value earlier than _last_discovery_times
        # This simulates the last discovery being in the future relative to "now"
        with patch("time.time", return_value=1000.0):
            listener._last_discovery_times["node123"] = (
                2000.0  # Set last discovery to be in the future
            )

            await listener._async_handle_discovery(
                _found("node123", ip="192.168.1.101", port=8080)
            )

        # Throttled: the entry is neither duplicated nor brought up to date.
        assert listener.devices == [_found("node123", port=8080, device_name="Device")]
