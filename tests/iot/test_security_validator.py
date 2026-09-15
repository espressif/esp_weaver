# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the security_validator module."""

from unittest.mock import AsyncMock, MagicMock, patch

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
        mock_module.get_transport = AsyncMock(return_value=MagicMock())
        mock_module.version_info = AsyncMock(return_value={"sec_ver": 0})

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
        mock_module.get_transport = AsyncMock(return_value=MagicMock())
        mock_module.version_info = AsyncMock(return_value={})
        return ESPSecurityManager(mock_module)

    @pytest.mark.asyncio
    async def test_no_transport(self, manager: ESPSecurityManager) -> None:
        """Test handling when transport creation fails (fail-secure)."""
        manager._local_ctrl.get_transport.return_value = None

        result = await manager.detect_device_security("192.168.1.100", 8080)

        # Fail-secure: assume security is required when transport unavailable
        assert result == {"security_version": 1, "pop_required": True}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("port", [443, 8080])
    async def test_http_transport_whatever_the_port(
        self, manager: ESPSecurityManager, port: int
    ) -> None:
        """ESP Local Control only speaks HTTP, even to a device listening on 443."""
        await manager.detect_device_security("192.168.1.100", port)

        manager._local_ctrl.get_transport.assert_awaited_once_with(
            "http", f"192.168.1.100:{port}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("version_block", "expected"),
        [
            # Newer firmware states sec_ver outright.
            ({"sec_ver": 0}, (0, False)),
            ({"sec_ver": 0, "cap": []}, (0, False)),
            ({"sec_ver": 1, "cap": ["no_pop"]}, (1, False)),
            ({"sec_ver": 1}, (1, True)),
            ({"sec_ver": 2, "cap": ["wifi_scan"]}, (2, True)),
            # Older firmware only lists capabilities.
            ({"cap": ["no_sec", "no_pop"]}, (0, False)),
            ({"cap": ["no_sec"]}, (0, False)),
            ({"cap": ["no_pop"]}, (1, False)),
            ({"cap": []}, (1, True)),
            # A device that answered with something that is not JSON.
            ({}, (1, True)),
        ],
    )
    async def test_the_version_document_decides(
        self,
        manager: ESPSecurityManager,
        version_block: dict,
        expected: tuple[int, bool],
    ) -> None:
        """Both what the device states and what it merely lists are honoured."""
        manager._local_ctrl.version_info.return_value = version_block

        result = await manager.detect_device_security("192.168.1.100", 8080)

        assert (result["security_version"], result["pop_required"]) == expected
        # One unencrypted read answers every question.
        manager._local_ctrl.version_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_caches_result(self, manager: ESPSecurityManager) -> None:
        """Test result is cached and reused on subsequent calls."""
        result1 = await manager.detect_device_security("192.168.1.100", 8080)
        result2 = await manager.detect_device_security("192.168.1.100", 8080)

        manager._local_ctrl.get_transport.assert_awaited_once()
        manager._local_ctrl.version_info.assert_awaited_once()
        assert result1 == result2 == {"security_version": 1, "pop_required": True}

    @pytest.mark.asyncio
    async def test_exception_handling(self, manager: ESPSecurityManager) -> None:
        """Test exception handling in detect_device_security (fail-secure)."""
        manager._local_ctrl.get_transport.side_effect = OSError("Network error")

        result = await manager.detect_device_security("192.168.1.100", 8080)

        # Fail-secure: assume security is required on connection errors
        assert result == {"security_version": 1, "pop_required": True}

    @pytest.mark.asyncio
    async def test_a_device_that_did_not_answer_is_not_cached(
        self, manager: ESPSecurityManager
    ) -> None:
        """A fallback is remembered by nobody, so the device is asked again."""
        manager._local_ctrl.version_info.return_value = None

        result = await manager.detect_device_security("192.168.1.100", 8080)
        assert result == {"security_version": 1, "pop_required": True}

        manager._local_ctrl.version_info.return_value = {"sec_ver": 0}
        result = await manager.detect_device_security("192.168.1.100", 8080)
        assert result == {"security_version": 0, "pop_required": False}

    @pytest.mark.asyncio
    async def test_a_transport_that_will_not_close_does_not_lose_the_answer(
        self, manager: ESPSecurityManager
    ) -> None:
        """The throwaway connection failing to close is not the device's fault."""
        mock_transport = MagicMock()
        mock_transport.close = MagicMock(side_effect=OSError("already gone"))
        manager._local_ctrl.get_transport.return_value = mock_transport
        manager._local_ctrl.version_info.return_value = {"sec_ver": 0}

        result = await manager.detect_device_security("192.168.1.100", 8080)

        mock_transport.close.assert_called_once()
        assert result == {"security_version": 0, "pop_required": False}


class TestAskDeviceSecurity:
    """What the device itself said, told apart from the fallback."""

    @pytest.fixture
    def manager(self) -> ESPSecurityManager:
        """Create a security manager with mocked module."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock(return_value=MagicMock())
        mock_module.version_info = AsyncMock(return_value={"sec_ver": 2})
        return ESPSecurityManager(mock_module)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("version", [0, 1, 2])
    async def test_an_answer_is_the_device_s_own(
        self, manager: ESPSecurityManager, version: int
    ) -> None:
        """A device that answered is reported with what it said, sec0 included."""
        manager._local_ctrl.version_info.return_value = {"sec_ver": version}

        assert await manager.ask_device_security("192.168.1.100", 8080) == version

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "make_silent",
        [
            lambda m: setattr(m.get_transport, "side_effect", OSError("refused")),
            lambda m: setattr(m.get_transport, "return_value", None),
            lambda m: setattr(m.version_info, "return_value", None),
            lambda m: setattr(m.version_info, "side_effect", RuntimeError("cut")),
        ],
        ids=["unreachable", "no transport", "no answer", "read failed"],
    )
    async def test_a_fallback_is_not_an_answer(
        self, manager: ESPSecurityManager, make_silent: object
    ) -> None:
        """Whatever kept the device from saying, None is reported - not sec1."""
        make_silent(manager._local_ctrl)  # type: ignore[operator]

        assert await manager.ask_device_security("192.168.1.100", 8080) is None
        # And the fallback still stands for the handshake's own use.
        assert (await manager.detect_device_security("192.168.1.100", 8080))[
            "security_version"
        ] == 1

    @pytest.mark.asyncio
    async def test_a_device_asked_again_can_answer_the_second_time(
        self, manager: ESPSecurityManager
    ) -> None:
        """Nothing is remembered of a silence, so a device that is now up is heard."""
        manager._local_ctrl.version_info.return_value = None
        assert await manager.ask_device_security("192.168.1.100", 8080) is None

        manager._local_ctrl.version_info.return_value = {"sec_ver": 0}
        assert await manager.ask_device_security("192.168.1.100", 8080) == 0


class TestPopConnection:
    """Test test_pop_connection method."""

    @pytest.fixture
    def manager(self) -> ESPSecurityManager:
        """Create a security manager with mocked module and pre-cached security info."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock()
        mock_module.build_security = AsyncMock(return_value=MagicMock())
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
        manager._local_ctrl.build_security.return_value = None

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
        manager._local_ctrl.establish_session.return_value = True

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection("192.168.1.100", "", 8080)

        assert result is True
        # Verify empty pop is passed correctly
        manager._local_ctrl.build_security.assert_awaited_once()
        assert manager._local_ctrl.build_security.call_args[0][2] == ""

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
        manager._local_ctrl.build_security.side_effect = TimeoutError("Security error")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False
        mock_transport.close.assert_called_once()


class TestPopConnectionSecurityNotCached:
    """What happens when detection has not left anything behind."""

    @pytest.mark.asyncio
    async def test_the_device_is_asked_again_rather_than_erroring(self) -> None:
        """Detection is not cached when it falls back, so this is reachable."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock(return_value=None)
        manager = ESPSecurityManager(mock_module)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is False
        assert mock_module.get_transport.await_count == 2

    @pytest.mark.asyncio
    async def test_a_device_that_answers_the_second_time_is_accepted(self) -> None:
        """The point of asking again is that it can now succeed."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock()
        mock_module.build_security = AsyncMock(return_value=MagicMock())
        mock_module.establish_session = AsyncMock(return_value=True)
        mock_module.version_info = AsyncMock(return_value={})
        manager = ESPSecurityManager(mock_module)

        transport = MagicMock()
        transport.close = MagicMock()
        mock_module.get_transport.return_value = transport

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection(
                "192.168.1.100", "test_pop", 8080
            )

        assert result is True


class TestPopConnectionTransportClosed:
    """The throwaway connection a PoP test opens is closed again."""

    @pytest.mark.asyncio
    async def test_the_transport_is_closed_after_the_handshake(self) -> None:
        """Whatever the handshake said, the connection is not left open."""
        mock_module = MagicMock()
        mock_module.get_transport = AsyncMock()
        mock_module.build_security = AsyncMock(return_value=MagicMock())
        mock_module.establish_session = AsyncMock()
        manager = ESPSecurityManager(mock_module)
        manager._cached_security_info[("192.168.1.100", 8080)] = {
            "security_version": 1,
            "pop_required": True,
        }

        mock_transport = MagicMock()
        mock_transport.close = MagicMock()
        mock_module.get_transport.return_value = mock_transport
        mock_module.establish_session.return_value = True

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.test_pop_connection("192.168.1.100", "pop", 8080)

        assert result is True
        mock_transport.close.assert_called_once()
