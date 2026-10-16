# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the availability_manager module."""

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.managers.availability_manager import (
    SERVICE_INFO_TIMEOUT_MS,
    TCP_CHECK_TIMEOUT,
    TCP_MAX_RETRIES,
    TCP_RETRY_INTERVAL,
    AvailabilityManager,
)
from tests.fakes import FakeHost


class TestAvailabilityManagerInit:
    """Test AvailabilityManager initialization."""

    @pytest.fixture
    def mock_host(self) -> FakeHost:
        """A host that records what the manager asks of it."""
        return FakeHost()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    def test_init(self, mock_host: FakeHost, mock_registry: MagicMock) -> None:
        """Test basic initialization."""
        manager = AvailabilityManager(mock_host, mock_registry)

        assert manager.host is mock_host
        assert manager.registry == mock_registry
        assert manager.default_port == 8080

    def test_init_custom_port(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test initialization with custom port."""
        manager = AvailabilityManager(mock_host, mock_registry, default_port=9000)

        assert manager.default_port == 9000


class TestCheckTcpPortReady:
    """Test AvailabilityManager.check_tcp_port_ready method."""

    @pytest.fixture
    def mock_host(self) -> FakeHost:
        """A host that records what the manager asks of it."""
        return FakeHost()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    async def test_port_ready(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test when port is ready."""
        manager = AvailabilityManager(mock_host, mock_registry)

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
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test when connection times out."""
        manager = AvailabilityManager(mock_host, mock_registry)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = TimeoutError()

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is False

    async def test_port_refused(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test when connection is refused."""
        manager = AvailabilityManager(mock_host, mock_registry)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = ConnectionRefusedError()

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is False

    async def test_os_error(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test handling OSError."""
        manager = AvailabilityManager(mock_host, mock_registry)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.side_effect = OSError("Network error")

            result = await manager.check_tcp_port_ready("192.168.1.100", 8080)

        assert result is False


class TestVerifyTcpConnectivity:
    """Test AvailabilityManager.verify_tcp_connectivity method."""

    @pytest.fixture
    def mock_host(self) -> FakeHost:
        """A host that records what the manager asks of it."""
        return FakeHost()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        registry = MagicMock()
        registry.is_device_available_async = AsyncMock(return_value=False)
        return registry

    async def test_immediate_success(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test immediate TCP connection success."""
        manager = AvailabilityManager(mock_host, mock_registry)

        with patch.object(
            manager, "check_tcp_port_ready", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = True

            result = await manager.verify_tcp_connectivity("192.168.1.100", 8080)

        assert result is True
        mock_check.assert_called_once()

    async def test_success_after_retries(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test TCP connection succeeds after retries."""
        manager = AvailabilityManager(mock_host, mock_registry)

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
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test all retries fail."""
        manager = AvailabilityManager(mock_host, mock_registry)

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
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test early exit when device becomes available."""
        mock_registry.is_device_available_async.return_value = True
        manager = AvailabilityManager(mock_host, mock_registry)

        result = await manager.verify_tcp_connectivity(
            "192.168.1.100", 8080, node_id="test_node"
        )

        assert result is True


class TestUpdateDeviceIpIfChanged:
    """Test AvailabilityManager._update_device_ip_if_changed method."""

    @pytest.fixture
    def mock_host(self) -> FakeHost:
        """A host that records what the manager asks of it."""
        return FakeHost()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        return MagicMock()

    def test_ip_unchanged(self, mock_host: FakeHost, mock_registry: MagicMock) -> None:
        """Test when IP is unchanged."""
        manager = AvailabilityManager(mock_host, mock_registry)

        result = manager._update_device_ip_if_changed(
            "node123", "192.168.1.100", "192.168.1.100"
        )

        assert result is False

    def test_ip_changed(self, mock_host: FakeHost, mock_registry: MagicMock) -> None:
        """Test when IP has changed."""
        mock_device = MagicMock(port=8080)
        mock_registry.get_device.return_value = mock_device
        manager = AvailabilityManager(mock_host, mock_registry)

        result = manager._update_device_ip_if_changed(
            "node123", "192.168.1.200", "192.168.1.100"
        )

        assert result is True
        # Moved through the registry, at the port it already had.
        mock_registry.register_device.assert_called_once_with(
            "node123", "192.168.1.200", 8080
        )

    def test_no_expected_ip(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test when expected IP is None."""
        manager = AvailabilityManager(mock_host, mock_registry)

        result = manager._update_device_ip_if_changed("node123", "192.168.1.100", None)

        assert result is False

    def test_device_not_found(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test when device not found in registry."""
        mock_registry.get_device.return_value = None
        manager = AvailabilityManager(mock_host, mock_registry)

        result = manager._update_device_ip_if_changed(
            "node123", "192.168.1.200", "192.168.1.100"
        )

        # IP changed but device not found - cannot update, returns False
        assert result is False


class TestConstants:
    """Test availability manager constants."""

    def test_mdns_timeout(self) -> None:
        """Test mDNS timeout is reasonable."""
        assert SERVICE_INFO_TIMEOUT_MS > 0
        assert SERVICE_INFO_TIMEOUT_MS <= 30_000

    def test_tcp_timeout(self) -> None:
        """Test TCP timeout is reasonable."""
        assert TCP_CHECK_TIMEOUT > 0
        assert TCP_CHECK_TIMEOUT <= 10

    def test_tcp_retries(self) -> None:
        """Test TCP retry settings are reasonable."""
        assert TCP_MAX_RETRIES > 0
        assert TCP_RETRY_INTERVAL > 0


class TestVerifyTcpConnectivityEdgeCases:
    """Test edge cases for verify_tcp_connectivity."""

    @pytest.fixture
    def mock_host(self) -> FakeHost:
        """A host that records what the manager asks of it."""
        return FakeHost()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry."""
        registry = MagicMock()
        registry.is_device_available_async = AsyncMock(return_value=False)
        return registry

    async def test_verify_with_zero_retries(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test verify with zero retries makes no attempts."""
        manager = AvailabilityManager(mock_host, mock_registry)

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
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test verify with custom retry interval."""
        manager = AvailabilityManager(mock_host, mock_registry)

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


class TestCheckDeviceMdnsAvailable:
    """One device asked for its own record by name, then for its port; no browser."""

    @pytest.fixture
    def mock_host(self) -> FakeHost:
        """A host that records what the manager asks of it."""
        return FakeHost()

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """A registry holding the device at its last known address."""
        registry = MagicMock()
        device = MagicMock()
        device.port = 8080
        device.ip = "192.168.1.100"
        registry.get_device.return_value = device
        return registry

    @pytest.fixture
    def info(self) -> Generator[MagicMock]:
        """The service record, answering with one address unless told otherwise."""
        with patch(
            "custom_components.esp_weaver.iot.managers.availability_manager.AsyncServiceInfo"
        ) as cls:
            record = cls.return_value
            record.async_request = AsyncMock(return_value=True)
            yield cls

    @pytest.fixture
    def ipv4(self) -> Generator[MagicMock]:
        """The IPv4 address read off the record."""
        with patch(
            "custom_components.esp_weaver.iot.managers.availability_manager.ipv4_of",
            return_value="192.168.1.100",
        ) as read:
            yield read

    async def test_the_record_is_asked_for_by_name(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """No browser: the one record, by the name the device advertises under."""
        manager = AvailabilityManager(mock_host, mock_registry)
        zeroconf = MagicMock()
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock) as get,
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as verify,
        ):
            get.return_value.zeroconf = zeroconf
            verify.return_value = True

            result = await manager.check_device_mdns_available("test_node")

        assert result is True
        info.assert_called_once_with(
            "_esp_local_ctrl._tcp.local.", "TEST_NODE._esp_local_ctrl._tcp.local."
        )
        info.return_value.async_request.assert_awaited_once_with(
            zeroconf, SERVICE_INFO_TIMEOUT_MS
        )
        verify.assert_awaited_once_with("192.168.1.100", 8080, node_id="test_node")

    async def test_a_device_that_does_not_answer_is_not_available(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """No answer within the time is the device being off the network."""
        manager = AvailabilityManager(mock_host, mock_registry)
        info.return_value.async_request.return_value = False
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock),
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as verify,
        ):
            result = await manager.check_device_mdns_available("test_node")

        assert result is False
        verify.assert_not_awaited()

    async def test_a_device_that_moved_is_followed(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """The record's address wins over the one the device was last seen at."""
        manager = AvailabilityManager(mock_host, mock_registry)
        ipv4.return_value = "192.168.1.200"
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock),
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as verify,
        ):
            verify.return_value = True

            result = await manager.check_device_mdns_available(
                "test_node", expected_ip="192.168.1.100"
            )

        assert result is True
        mock_registry.register_device.assert_called_once_with(
            "test_node", "192.168.1.200", 8080
        )
        verify.assert_awaited_once_with("192.168.1.200", 8080, node_id="test_node")

    async def test_a_record_without_an_address_falls_back_to_the_registry(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """A record that answered but carried no IPv4 is checked where it was."""
        manager = AvailabilityManager(mock_host, mock_registry)
        ipv4.return_value = None
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock),
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as verify,
        ):
            verify.return_value = True

            result = await manager.check_device_mdns_available("test_node")

        assert result is True
        verify.assert_awaited_once_with("192.168.1.100", 8080, node_id="test_node")

    async def test_no_address_anywhere_is_not_available(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """Nothing to connect to, so nothing to call available."""
        mock_registry.get_device.return_value = None
        manager = AvailabilityManager(mock_host, mock_registry)
        ipv4.return_value = None
        with patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock):
            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_a_port_that_never_opens_is_not_available(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """Answering mDNS is not the same as taking connections."""
        manager = AvailabilityManager(mock_host, mock_registry)
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock),
            patch.object(
                manager, "verify_tcp_connectivity", new_callable=AsyncMock
            ) as verify,
        ):
            verify.return_value = False

            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    @pytest.mark.parametrize(
        "failure",
        [OSError("Network error"), RuntimeError("loop closed"), ImportError("zc")],
        ids=["network", "loop", "import"],
    )
    async def test_a_failure_asking_is_not_available(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
        failure: Exception,
    ) -> None:
        """Whatever kept the question from being asked, the answer is no."""
        manager = AvailabilityManager(mock_host, mock_registry)
        with patch.object(mock_host, "async_zeroconf", side_effect=failure):
            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_a_failure_verifying_is_not_available(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """A port check that breaks is reported as no, not raised."""
        manager = AvailabilityManager(mock_host, mock_registry)
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock),
            patch.object(
                manager,
                "verify_tcp_connectivity",
                new_callable=AsyncMock,
                side_effect=OSError("reset"),
            ),
        ):
            result = await manager.check_device_mdns_available("test_node")

        assert result is False

    async def test_cancellation_is_passed_on(
        self,
        mock_host: FakeHost,
        mock_registry: MagicMock,
        info: MagicMock,
        ipv4: MagicMock,
    ) -> None:
        """A cancelled check is cancelled, not written down as offline."""
        manager = AvailabilityManager(mock_host, mock_registry)
        info.return_value.async_request.side_effect = asyncio.CancelledError()
        with (
            patch.object(mock_host, "async_zeroconf", new_callable=AsyncMock),
            pytest.raises(asyncio.CancelledError),
        ):
            await manager.check_device_mdns_available("test_node")
