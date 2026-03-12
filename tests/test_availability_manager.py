# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the availability_manager module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zeroconf import ServiceStateChange

from custom_components.esp_weaver.iot.managers.availability_manager import (
    MDNS_DETECTION_TIMEOUT,
    TCP_CHECK_TIMEOUT,
    TCP_MAX_RETRIES,
    TCP_RETRY_INTERVAL,
    AvailabilityManager,
    MDNSDetectionResult,
)


class TestMDNSDetectionResult:
    """Test MDNSDetectionResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = MDNSDetectionResult()

        assert result.detected is False
        assert result.ip_address is None

    def test_custom_values(self) -> None:
        """Test custom values."""
        result = MDNSDetectionResult(detected=True, ip_address="192.168.1.100")

        assert result.detected is True
        assert result.ip_address == "192.168.1.100"


class TestAvailabilityManagerInit:
    """Test AvailabilityManager initialization."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    def test_init(self, mock_hass: MagicMock, mock_registry: MagicMock) -> None:
        """Test basic initialization."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        assert manager.hass == mock_hass
        assert manager.registry == mock_registry
        assert manager.default_port == 8080

    def test_init_custom_port(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test initialization with custom port."""
        manager = AvailabilityManager(mock_hass, mock_registry, default_port=9000)

        assert manager.default_port == 9000


class TestCheckTcpPortReady:
    """Test AvailabilityManager.check_tcp_port_ready method."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    async def test_port_ready(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test when port is ready."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.return_value = (MagicMock(), mock_writer)

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is True
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()

    async def test_port_timeout(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test when connection times out."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = TimeoutError()

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is False

    async def test_port_refused(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test when connection is refused."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = ConnectionRefusedError()

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is False

    async def test_os_error(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test handling OSError."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = OSError("Network error")

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is False


class TestVerifyTcpConnectivity:
    """Test AvailabilityManager.verify_tcp_connectivity method."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        registry = MagicMock()
        registry.is_device_available.return_value = False
        return registry

    async def test_immediate_success(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test immediate TCP connection success."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch.object(
            manager, "check_tcp_port_ready", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = True

            result = await manager.verify_tcp_connectivity("192.168.1.100", 8080)

        assert result is True
        mock_check.assert_called_once()

    async def test_success_after_retries(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test TCP connection succeeds after retries."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch.object(
            manager, "check_tcp_port_ready", new_callable=AsyncMock
        ) as mock_check:
            # Fail twice, then succeed
            mock_check.side_effect = [False, False, True]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await manager.verify_tcp_connectivity(
                    "192.168.1.100", 8080, max_retries=5
                )

        assert result is True
        assert mock_check.call_count == 3

    async def test_all_retries_fail(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test all retries fail."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch.object(
            manager, "check_tcp_port_ready", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = False

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await manager.verify_tcp_connectivity(
                    "192.168.1.100", 8080, max_retries=3
                )

        assert result is False
        assert mock_check.call_count == 3

    async def test_early_exit_when_device_available(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test early exit when device becomes available."""
        mock_registry.is_device_available.return_value = True
        manager = AvailabilityManager(mock_hass, mock_registry)

        result = await manager.verify_tcp_connectivity(
            "192.168.1.100", 8080, node_id="test_node"
        )

        assert result is True


class TestUpdateDeviceIpIfChanged:
    """Test AvailabilityManager._update_device_ip_if_changed method."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    def test_ip_unchanged(self, mock_hass: MagicMock, mock_registry: MagicMock) -> None:
        """Test when IP is unchanged."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        result = manager._update_device_ip_if_changed(
            "node123", "192.168.1.100", "192.168.1.100"
        )

        assert result is False

    def test_ip_changed(self, mock_hass: MagicMock, mock_registry: MagicMock) -> None:
        """Test when IP has changed."""
        mock_device = MagicMock()
        mock_registry.get_device.return_value = mock_device
        manager = AvailabilityManager(mock_hass, mock_registry)

        result = manager._update_device_ip_if_changed(
            "node123", "192.168.1.200", "192.168.1.100"
        )

        assert result is True
        assert mock_device.ip == "192.168.1.200"

    def test_no_expected_ip(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test when expected IP is None."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        result = manager._update_device_ip_if_changed("node123", "192.168.1.100", None)

        assert result is False

    def test_device_not_found(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test when device not found in registry."""
        mock_registry.get_device.return_value = None
        manager = AvailabilityManager(mock_hass, mock_registry)

        result = manager._update_device_ip_if_changed(
            "node123", "192.168.1.200", "192.168.1.100"
        )

        # IP changed but device not found - cannot update, returns False
        assert result is False


class TestWaitForMdnsDetection:
    """Test AvailabilityManager._wait_for_mdns_detection method."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    async def test_detected_before_timeout(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test detection before timeout."""
        manager = AvailabilityManager(mock_hass, mock_registry)
        event = asyncio.Event()
        event.set()

        result = await manager._wait_for_mdns_detection(event, timeout=1.0)

        assert result is True

    async def test_timeout(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test timeout when not detected returns False."""
        manager = AvailabilityManager(mock_hass, mock_registry)
        event = asyncio.Event()
        # Don't set the event - should timeout

        # The function catches TimeoutError internally and returns False
        result = await manager._wait_for_mdns_detection(event, timeout=0.01)
        assert result is False


class TestConstants:
    """Test availability manager constants."""

    def test_mdns_timeout(self) -> None:
        """Test mDNS timeout is reasonable."""
        assert MDNS_DETECTION_TIMEOUT > 0
        assert MDNS_DETECTION_TIMEOUT <= 30

    def test_tcp_timeout(self) -> None:
        """Test TCP timeout is reasonable."""
        assert TCP_CHECK_TIMEOUT > 0
        assert TCP_CHECK_TIMEOUT <= 10

    def test_tcp_retries(self) -> None:
        """Test TCP retry settings are reasonable."""
        assert TCP_MAX_RETRIES > 0
        assert TCP_RETRY_INTERVAL > 0


class TestAsyncServiceUpdateCallback:
    """Test AvailabilityManager._async_service_update_callback method."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    async def test_name_mismatch(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test callback ignores mismatched service names."""
        manager = AvailabilityManager(mock_hass, mock_registry)
        result = MDNSDetectionResult()
        event = asyncio.Event()
        mock_zc = MagicMock()

        await manager._async_service_update_callback(
            mock_zc,
            "_esp_local_ctrl._tcp.local.",
            "OTHER_SERVICE._esp_local_ctrl._tcp.local.",
            ServiceStateChange.Added,
            "TARGET_SERVICE._esp_local_ctrl._tcp.local.",
            result,
            event,
        )

        assert result.detected is False
        assert not event.is_set()

    async def test_removed_state_ignored(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test callback ignores removed state changes."""
        manager = AvailabilityManager(mock_hass, mock_registry)
        result = MDNSDetectionResult()
        event = asyncio.Event()
        mock_zc = MagicMock()

        await manager._async_service_update_callback(
            mock_zc,
            "_esp_local_ctrl._tcp.local.",
            "SERVICE._esp_local_ctrl._tcp.local.",
            ServiceStateChange.Removed,
            "SERVICE._esp_local_ctrl._tcp.local.",
            result,
            event,
        )

        assert result.detected is False
        assert not event.is_set()

    async def test_added_state_with_service_info(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test callback handles added state with service info."""
        manager = AvailabilityManager(mock_hass, mock_registry)
        result = MDNSDetectionResult()
        event = asyncio.Event()
        mock_zc = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceInfo"
        ) as mock_info_class:
            mock_info = MagicMock()
            mock_info.async_request = AsyncMock(return_value=True)
            mock_info.addresses = [b"\xc0\xa8\x01\x64"]  # 192.168.1.100
            mock_info_class.return_value = mock_info

            await manager._async_service_update_callback(
                mock_zc,
                "_esp_local_ctrl._tcp.local.",
                "SERVICE._esp_local_ctrl._tcp.local.",
                ServiceStateChange.Added,
                "SERVICE._esp_local_ctrl._tcp.local.",
                result,
                event,
            )

            assert result.detected is True
            assert result.ip_address == "192.168.1.100"
            assert event.is_set()

    async def test_added_state_service_info_error(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test callback handles service info error."""
        manager = AvailabilityManager(mock_hass, mock_registry)
        result = MDNSDetectionResult()
        event = asyncio.Event()
        mock_zc = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceInfo"
        ) as mock_info_class:
            mock_info = MagicMock()
            mock_info.async_request = AsyncMock(side_effect=OSError())
            mock_info_class.return_value = mock_info

            await manager._async_service_update_callback(
                mock_zc,
                "_esp_local_ctrl._tcp.local.",
                "SERVICE._esp_local_ctrl._tcp.local.",
                ServiceStateChange.Added,
                "SERVICE._esp_local_ctrl._tcp.local.",
                result,
                event,
            )

            assert result.detected is False


class TestCheckDeviceMdnsAvailable:
    """Test AvailabilityManager.check_device_mdns_available method."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()  # Mock loop to avoid deprecated get_event_loop()
        hass.async_create_task = MagicMock()
        return hass

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        registry = MagicMock()
        mock_device = MagicMock()
        mock_device.port = 8080
        mock_device.ip = "192.168.1.100"
        registry.get_device.return_value = mock_device
        return registry

    async def test_check_mdns_os_error(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test check_device_mdns_available handles OSError."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        # Patch the internal import that happens inside the method
        with patch(
            "homeassistant.components.zeroconf.async_get_async_instance",
            side_effect=OSError("Network error"),
        ):
            result = await manager.check_device_mdns_available("test_node")

            assert result is False


class TestVerifyTcpConnectivityEdgeCases:
    """Test edge cases for verify_tcp_connectivity."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        registry = MagicMock()
        registry.is_device_available.return_value = False
        return registry

    async def test_verify_with_zero_retries(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test verify with zero retries makes no attempts."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch.object(
            manager, "check_tcp_port_ready", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = False

            # max_retries=0 means zero total attempts (range(0) is empty)
            result = await manager.verify_tcp_connectivity(
                "192.168.1.100", 8080, max_retries=0
            )

            assert result is False
            # Should not have called check at all with 0 retries
            assert mock_check.call_count == 0

    async def test_verify_with_custom_interval(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test verify with custom retry interval."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch.object(
            manager, "check_tcp_port_ready", new_callable=AsyncMock
        ) as mock_check:
            mock_check.side_effect = [False, True]

            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await manager.verify_tcp_connectivity(
                    "192.168.1.100", 8080, max_retries=3, retry_interval=0.5
                )

                assert result is True
                mock_sleep.assert_called_with(0.5)


class TestCheckDeviceMdnsAvailableFullPath:
    """Test full execution path of check_device_mdns_available."""

    @pytest.fixture
    def mock_hass(self) -> MagicMock:
        """Create mock Home Assistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()  # Mock loop to avoid deprecated get_event_loop()
        hass.loop.call_soon_threadsafe = MagicMock()
        hass.async_create_task = MagicMock()
        return hass

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        registry = MagicMock()
        mock_device = MagicMock()
        mock_device.port = 8080
        mock_device.ip = "192.168.1.100"
        registry.get_device.return_value = mock_device
        return registry

    async def test_mdns_available_success(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test successful mDNS availability check."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        # Mock the detection to succeed immediately
        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as mock_verify,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.return_value = True
            mock_verify.return_value = True

            result = await manager.check_device_mdns_available("test_node")

        assert result is True

    async def test_mdns_detection_timeout(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test mDNS detection timeout returns False."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.return_value = False  # Timeout

            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_no_ip_from_detection_uses_registry(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test using registry IP when detection doesn't return IP."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as mock_verify,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.return_value = True  # Detected but no IP in result
            mock_verify.return_value = True

            result = await manager.check_device_mdns_available("test_node")

        # Should succeed using registry IP
        assert result is True

    async def test_mdns_no_ip_and_no_device(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test no IP available from detection or registry."""
        mock_registry.get_device.return_value = None
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.return_value = True  # Detected but no IP

            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_tcp_not_ready(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test mDNS detected but TCP not ready."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as mock_verify,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.return_value = True
            mock_verify.return_value = False  # TCP not ready

            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_os_error_in_try_block(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test OSError handling in inner try block."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.side_effect = OSError("Network error")

            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_timeout_error_in_try_block(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test TimeoutError handling in inner try block."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.side_effect = TimeoutError()

            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_cancelled_error_in_try_block(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test CancelledError handling in inner try block."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.side_effect = asyncio.CancelledError()

            # CancelledError should be re-raised for proper task cancellation propagation
            with pytest.raises(asyncio.CancelledError):
                await manager.check_device_mdns_available("test_node")

    async def test_mdns_import_error(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test ImportError handling."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch(
            "homeassistant.components.zeroconf.async_get_async_instance",
            new_callable=AsyncMock,
            side_effect=ImportError("zeroconf not available"),
        ):
            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_runtime_error(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test RuntimeError handling."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        with patch(
            "homeassistant.components.zeroconf.async_get_async_instance",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Event loop error"),
        ):
            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_mdns_detection_with_tcp_verification(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test mDNS detection success with TCP connectivity verification."""
        manager = AvailabilityManager(mock_hass, mock_registry)

        # Set up device in registry with IP
        mock_device = MagicMock()
        mock_device.ip = "192.168.1.100"
        mock_device.port = 8080
        mock_registry.get_device.return_value = mock_device

        mock_browser = MagicMock()
        mock_browser.async_cancel = AsyncMock()

        with (
            patch(
                "homeassistant.components.zeroconf.async_get_async_instance",
                new_callable=AsyncMock,
            ) as mock_get_zc,
            patch(
                "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceBrowser",
                return_value=mock_browser,
            ),
            patch.object(
                manager, "_wait_for_mdns_detection", new_callable=AsyncMock
            ) as mock_wait,
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as mock_verify,
        ):
            mock_aiozc = MagicMock()
            mock_aiozc.zeroconf = MagicMock()
            mock_get_zc.return_value = mock_aiozc

            mock_wait.return_value = True
            mock_verify.return_value = True

            result = await manager.check_device_mdns_available(
                "test_node", expected_ip="192.168.1.50"
            )

        assert result is True
        # verify_tcp_connectivity was called with the device's registered IP
        mock_verify.assert_called_once()
