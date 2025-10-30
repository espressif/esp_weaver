# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the security_validator module."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from custom_components.esp_weaver.iot.client.security_validator import (
    ESPSecurityManager,
)


class TestESPSecurityManagerInit:
    """Test ESPSecurityManager initialization."""

    def test_init(self) -> None:
        """Test basic initialization."""
        mock_module = MagicMock()

        manager = ESPSecurityManager(mock_module)

        assert manager._local_ctrl == mock_module

    @pytest.mark.asyncio
    async def test_clear_cache(self) -> None:
        """Test clearing cached security info via public API behavior."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock()
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        mock_module.get_transport.return_value = mock_transport
        # no_sec = True, no_pop = True (no security)
        mock_module.has_capability = AsyncMock(return_value=True)

        manager = ESPSecurityManager(mock_module)

        # First call populates cache
        await manager.detect_device_security("192.168.1.100", 8080)
        assert mock_module.get_transport.call_count == 1

        # Second call should use cache (no additional transport call)
        await manager.detect_device_security("192.168.1.100", 8080)
        assert mock_module.get_transport.call_count == 1

        # Clear cache
        manager.clear_cache()

        # Third call should hit transport again (cache was cleared)
        await manager.detect_device_security("192.168.1.100", 8080)
        assert mock_module.get_transport.call_count == 2


class TestDetectDeviceSecurity:
    """Test detect_device_security method."""

    @pytest.fixture
    def manager(self) -> ESPSecurityManager:
        """Create a security manager with mocked module."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock()
        mock_module.has_capability = AsyncMock()
        return ESPSecurityManager(mock_module)

    @pytest.mark.asyncio
    async def test_no_transport(self, manager: ESPSecurityManager) -> None:
        """Test handling when transport creation fails (fail-secure)."""
        manager._local_ctrl.get_transport.return_value = None

        result = await manager.detect_device_security("192.168.1.100", 8080)

        # Fail-secure: assume security is required when transport unavailable
        assert result == {"security_version": 1, "pop_required": True}

    @pytest.mark.asyncio
    async def test_http_transport_on_port_443(
        self, manager: ESPSecurityManager
    ) -> None:
        """Test HTTP transport type is used even for port 443.

        ESP Local Control only supports HTTP transport - even when devices
        listen on 443, the transport implementation is HTTP (not HTTPS).
        """
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.has_capability.side_effect = [False, False]

        await manager.detect_device_security("192.168.1.100", 443)

        manager._local_ctrl.get_transport.assert_called_once()
        call_args = manager._local_ctrl.get_transport.call_args
        # HTTP transport is expected even on 443 - ESP Local Control doesn't use HTTPS
        assert call_args[0][0] == "http"

    @pytest.mark.asyncio
    async def test_http_port_8080(self, manager: ESPSecurityManager) -> None:
        """Test HTTP transport type for port 8080."""
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.has_capability.side_effect = [True, True]

        await manager.detect_device_security("192.168.1.100", 8080)

        manager._local_ctrl.get_transport.assert_called_once()
        call_args = manager._local_ctrl.get_transport.call_args
        assert call_args[0][0] == "http"

    @pytest.mark.asyncio
    async def test_security_version_0_no_security(
        self, manager: ESPSecurityManager
    ) -> None:
        """Test detecting security version 0 (no security)."""
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        manager._local_ctrl.get_transport.return_value = mock_transport
        # no_sec = True, no_pop = True
        manager._local_ctrl.has_capability.side_effect = [True, True]

        result = await manager.detect_device_security("192.168.1.100", 8080)

        assert result["security_version"] == 0
        assert result["pop_required"] is False

        # Verify protocol capability checks
        manager._local_ctrl.has_capability.assert_has_calls(
            [
                call(mock_transport, "no_sec"),
                call(mock_transport, "no_pop"),
            ]
        )

    @pytest.mark.asyncio
    async def test_security_version_1_with_pop(
        self, manager: ESPSecurityManager
    ) -> None:
        """Test detecting security version 1 with PoP required."""
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        manager._local_ctrl.get_transport.return_value = mock_transport
        # no_sec = False, no_pop = False
        manager._local_ctrl.has_capability.side_effect = [False, False]

        result = await manager.detect_device_security("192.168.1.100", 8080)

        assert result["security_version"] == 1
        assert result["pop_required"] is True

    @pytest.mark.asyncio
    async def test_security_version_1_no_pop(self, manager: ESPSecurityManager) -> None:
        """Test detecting security version 1 without PoP."""
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        manager._local_ctrl.get_transport.return_value = mock_transport
        # no_sec = False, no_pop = True
        manager._local_ctrl.has_capability.side_effect = [False, True]

        result = await manager.detect_device_security("192.168.1.100", 8080)

        assert result["security_version"] == 1
        assert result["pop_required"] is False

    @pytest.mark.asyncio
    async def test_caches_result(self, manager: ESPSecurityManager) -> None:
        """Test result is cached and reused on subsequent calls."""
        mock_transport = MagicMock()
        mock_transport.send_data = AsyncMock(return_value="{}")
        manager._local_ctrl.get_transport.return_value = mock_transport
        # no_sec = False, no_pop = False
        manager._local_ctrl.has_capability.side_effect = [False, False]

        # First call should invoke transport and capability checks
        result1 = await manager.detect_device_security("192.168.1.100", 8080)
        manager._local_ctrl.get_transport.assert_called_once()
        assert manager._local_ctrl.has_capability.call_count == 2

        # Second call should reuse cache - no additional transport calls
        result2 = await manager.detect_device_security("192.168.1.100", 8080)

        # Verify transport was NOT called again (still only 1 call)
        manager._local_ctrl.get_transport.assert_called_once()
        # Protocol checks should also not be called again
        assert manager._local_ctrl.has_capability.call_count == 2

        # Results should be identical
        assert result1 == result2
        assert result1["security_version"] == 1
        assert result1["pop_required"] is True

    @pytest.mark.asyncio
    async def test_exception_handling(self, manager: ESPSecurityManager) -> None:
        """Test exception handling in detect_device_security (fail-secure)."""
        manager._local_ctrl.get_transport.side_effect = OSError("Network error")

        result = await manager.detect_device_security("192.168.1.100", 8080)

        # Fail-secure: assume security is required on connection errors
        assert result == {"security_version": 1, "pop_required": True}


class TestPopConnection:
    """Test test_pop_connection method."""

    @pytest.fixture
    def manager(self) -> ESPSecurityManager:
        """Create a security manager with mocked module and pre-cached security info."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock()
        mock_module.get_security = MagicMock()
        mock_module.establish_session = AsyncMock()
        mgr = ESPSecurityManager(mock_module)
        # Pre-populate cache so test_pop_connection can be called
        mgr._cached_security_info[("192.168.1.100", 8080)] = {
            "security_version": 1,
            "pop_required": True,
        }
        return mgr

    @pytest.mark.asyncio
    async def test_successful_pop(self, manager: ESPSecurityManager) -> None:
        """Test successful PoP validation."""
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.get_security.return_value = MagicMock()
        manager._local_ctrl.establish_session.return_value = True

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is True
        mock_transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_transport(self, manager: ESPSecurityManager) -> None:
        """Test handling when transport creation fails."""
        manager._local_ctrl.get_transport.return_value = None

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_failed_security_context(self, manager: ESPSecurityManager) -> None:
        """Test handling when security context creation fails."""
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.get_security.return_value = None

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False
        mock_transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_session(self, manager: ESPSecurityManager) -> None:
        """Test handling when session establishment fails."""
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.get_security.return_value = MagicMock()
        manager._local_ctrl.establish_session.return_value = False

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False
        mock_transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_pop(self, manager: ESPSecurityManager) -> None:
        """Test PoP connection with empty PoP string."""
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.get_security.return_value = MagicMock()
        manager._local_ctrl.establish_session.return_value = True

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection("192.168.1.100", "", 8080)

        assert result is True
        # Verify empty pop is passed correctly
        manager._local_ctrl.get_security.assert_called_once()
        call_kwargs = manager._local_ctrl.get_security.call_args[1]
        assert call_kwargs["pop"] == ""

    @pytest.mark.asyncio
    async def test_pop_exception_handling(self, manager: ESPSecurityManager) -> None:
        """Test exception handling in test_pop_connection."""
        manager._local_ctrl.get_transport.side_effect = ConnectionError(
            "Connection error"
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_transport_cleanup_on_error(
        self, manager: ESPSecurityManager
    ) -> None:
        """Test transport is cleaned up even on error."""
        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.get_security.side_effect = TimeoutError("Security error")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False
        mock_transport.close.assert_called_once()
