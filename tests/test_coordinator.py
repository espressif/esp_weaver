# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver coordinator."""

import asyncio
import contextlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esp_weaver.const import (
    CLOUD_CONNECTIVITY_CHECK_POLLS,
    CONF_IDENTITY_ADVERTISED,
    RECONNECT_DELAYS,
)
from custom_components.esp_weaver.coordinator import ESPDataUpdateCoordinator
from custom_components.esp_weaver.helpers.signals import signal_connection_error
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.iot.managers.device_registry import DeviceInfo
from custom_components.esp_weaver.polling import CloudPoller
from custom_components.esp_weaver.profiles import DeviceIdentity

from .conftest import (
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    TEST_PORT,
    create_mock_config_entry,
)

IDENTITY = DeviceIdentity.of(TEST_NODE_ID, None)


def _registered_device(ip: str = TEST_HOST, port: int = TEST_PORT) -> DeviceInfo:
    """Return what the registry holds about the test node."""
    return DeviceInfo(node_id=TEST_NODE_ID, ip=ip, port=port)


class TestCoordinatorInitialization:
    """Test coordinator initialization."""

    async def test_coordinator_init(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test coordinator initialization."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        assert coordinator.node_id == TEST_NODE_ID
        assert coordinator.device_name == TEST_DEVICE_NAME
        assert coordinator.is_available is False

    async def test_coordinator_discovered_entities_init(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test the entity cache starts empty and fills in per platform."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        assert coordinator.discovered_entities == {}


class TestCoordinatorDataUpdate:
    """Test coordinator data update logic."""

    async def test_update_data_device_available(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful data update when device is available."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()

        assert coordinator.is_available is True

    async def test_update_data_device_offline(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test data update when device is offline."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.get_device = MagicMock(return_value=_registered_device())

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()

        assert coordinator.is_available is False

    async def test_update_data_network_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A poll that got nothing back stops claiming the device is available."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=OSError("Network unreachable")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()
        assert coordinator.is_available is False
        assert coordinator._reconnecting

        # However many times: unavailable, reconnecting, and no exception for
        # Home Assistant to log, since the entities already say so.
        await coordinator._async_update_data()
        await coordinator._async_update_data()
        assert coordinator.is_available is False

    async def test_update_data_timeout_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A timeout is read the same way as any other failed poll."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=TimeoutError("Connection timed out")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()
        assert coordinator.is_available is False


class TestCoordinatorDiscovery:
    """Test coordinator entity discovery."""

    async def test_initial_discovery(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
        mock_device_properties: list[dict[str, Any]],
    ) -> None:
        """Test initial entity discovery."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.read_properties = AsyncMock(return_value=mock_device_properties)

        # Track discovery state - starts as False, becomes True after mark_discovery_completed
        discovery_state = {"completed": False}

        def is_discovery_completed(node_id: str) -> bool:
            return discovery_state["completed"]

        def mark_discovery_completed(node_id: str) -> None:
            discovery_state["completed"] = True

        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(
            side_effect=is_discovery_completed
        )
        mock_esp_api.mark_discovery_completed = MagicMock(
            side_effect=mark_discovery_completed
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()

        # Discovery should be triggered
        mock_esp_api.parse_and_discover_entities.assert_called_once()
        mock_esp_api.mark_discovery_completed.assert_called_once_with(TEST_NODE_ID)

    async def test_skip_discovery_if_completed(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test discovery is skipped if already completed."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        await coordinator._async_update_data()

        # Discovery should not be triggered again
        mock_esp_api.parse_and_discover_entities.assert_not_called()


class TestCoordinatorReconnection:
    """Test coordinator reconnection logic."""

    async def test_reconnection_scheduled_on_offline(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection is scheduled when device goes offline."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.is_mdns_available = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()

        # Device should be marked unavailable
        assert not coordinator.is_available
        # Reconnection task should be created
        assert coordinator._reconnect_task is not None

        # Cleanup: cancel the reconnect task to prevent resource leaks
        if coordinator._reconnect_task is not None:
            coordinator._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await coordinator._reconnect_task

    async def test_reconnection_success(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful reconnection."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_mdns_available = AsyncMock(return_value=True)
        mock_esp_api.register_device = AsyncMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # Simulate device becoming unavailable
        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is True
        assert coordinator.is_available is True


class TestCoordinatorShutdown:
    """Test coordinator shutdown."""

    async def test_shutdown_cancels_reconnect_task(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test shutdown cancels pending reconnection task."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # Create a mock reconnect task
        async def mock_reconnect():
            await asyncio.sleep(10)

        coordinator._reconnect_task = hass.async_create_task(mock_reconnect())

        await coordinator.async_shutdown()

        # Task should be cancelled
        assert coordinator._reconnect_task is None


class TestCoordinatorConnectionErrorHandling:
    """Test connection error event handling."""

    async def test_connection_error_triggers_reconnection(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test connection error event triggers reconnection."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # Simulate connection error signal
        coordinator._handle_connection_error()

        assert coordinator.is_available is False

    async def test_connection_error_ignored_for_other_device(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test connection error for other device is ignored.

        The coordinator listens on its own node's signal, so one dispatched
        for another node never reaches it.
        """
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        original_available = coordinator.is_available
        # What the signal does once heard is reconnection's business, tested
        # elsewhere; here only whether it is heard at all.
        with patch.object(coordinator, "_schedule_reconnection") as schedule:
            async_dispatcher_send(hass, signal_connection_error("different_node"))
            await hass.async_block_till_done()
            assert coordinator.is_available == original_available
            schedule.assert_not_called()

            async_dispatcher_send(hass, signal_connection_error(TEST_NODE_ID))
            await hass.async_block_till_done()
            assert coordinator.is_available is False
            schedule.assert_called_once()
        await coordinator.async_shutdown()

    async def test_connection_error_ignored_when_reconnecting(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test connection error ignored when reconnection already in progress."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        async def _still_trying() -> bool:
            await asyncio.sleep(10)
            return False

        coordinator._last_available = True
        pending = hass.async_create_task(_still_trying())
        coordinator._reconnect_task = pending

        coordinator._handle_connection_error()

        # Still available: an attempt is already pending.
        assert coordinator._last_available is True
        pending.cancel()


class TestCoordinatorReconnectionEdgeCases:
    """Test coordinator reconnection edge cases."""

    async def test_schedule_reconnection_already_scheduled(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection not scheduled when task already pending."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # Create a mock pending task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        coordinator._reconnect_task = mock_task

        # Try to schedule another reconnection
        coordinator._schedule_reconnection()

        # Should not create new task
        assert coordinator._reconnect_task == mock_task

    async def test_delayed_reconnection_clears_task(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test delayed reconnection clears task reference after completion."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.get_device = MagicMock(return_value=_registered_device())

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        # Run delayed reconnection with mocked sleep to avoid CI flakiness
        # Note: asyncio.sleep is mocked, so the delay value is irrelevant;
        # using RECONNECT_DELAYS[0] here for documentation purposes
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coordinator._delayed_reconnection(RECONNECT_DELAYS[0])

        # Task reference should be cleared (in finally block)
        assert coordinator._reconnect_task is None

    async def test_attempt_reconnection_already_available(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection returns True when already available."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = True

        result = await coordinator._attempt_single_reconnection()

        assert result is True
        assert coordinator._reconnect_attempt == 0

    async def test_attempt_reconnection_no_device_info(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection fails when no device info."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=None)  # Unknown node

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False

    async def test_attempt_reconnection_no_ip(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection fails when no IP address."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device(ip=""))

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False

    async def test_attempt_reconnection_via_mdns(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection via mDNS when not available."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.is_mdns_available = AsyncMock(return_value=True)
        mock_esp_api.register_device = AsyncMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is True
        mock_esp_api.is_mdns_available.assert_called_once()
        mock_esp_api.register_device.assert_called_once()

    async def test_a_device_that_moved_is_dialled_at_its_new_address(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """The mDNS check records a moved device's new address; that is what is dialled."""
        entry = create_mock_config_entry(mock_config_entry_data)
        new_ip = "192.168.1.250"
        device = _registered_device()
        mock_esp_api.get_device = MagicMock(return_value=device)
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)

        async def move_device(node_id: str, ip: str) -> bool:
            """Answer as the mDNS check does when it finds the node elsewhere."""
            assert ip == TEST_HOST
            mock_esp_api.get_device = MagicMock(
                return_value=_registered_device(ip=new_ip)
            )
            return True

        mock_esp_api.is_mdns_available = AsyncMock(side_effect=move_device)
        mock_esp_api.register_device = AsyncMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is True
        mock_esp_api.register_device.assert_awaited_once_with(
            TEST_NODE_ID, new_ip, TEST_PORT
        )

    async def test_a_device_that_vanished_after_the_mdns_check_is_not_dialled(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A registry record gone by the time it is read back means no address to dial."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.get_device = MagicMock(side_effect=[_registered_device(), None])
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.is_mdns_available = AsyncMock(return_value=True)
        mock_esp_api.register_device = AsyncMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False
        mock_esp_api.register_device.assert_not_awaited()

    async def test_attempt_reconnection_mdns_fails(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection fails when mDNS fails."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.is_mdns_available = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False

    async def test_attempt_reconnection_os_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection handles OSError."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=OSError("Network error")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        # Should not crash, return False
        assert result is False

    async def test_attempt_reconnection_timeout_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection handles TimeoutError."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(side_effect=TimeoutError())

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False


class TestScheduleNextReconnection:
    """Test _schedule_next_reconnection_if_needed method."""

    async def test_schedule_next_with_remaining_retries(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test scheduling next reconnection with retries remaining."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._reconnect_attempt = 0

        with patch.object(coordinator, "_schedule_reconnection") as mock_schedule:
            coordinator._schedule_next_reconnection_if_needed()

            mock_schedule.assert_called_once()
            assert coordinator._reconnect_attempt == 1

    async def test_schedule_next_no_remaining_retries(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reset when no retries remaining."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # Set attempt to max
        coordinator._reconnect_attempt = len(RECONNECT_DELAYS)

        with patch.object(coordinator, "_schedule_reconnection") as mock_schedule:
            coordinator._schedule_next_reconnection_if_needed()

            # Should not schedule, should reset
            mock_schedule.assert_not_called()
            assert coordinator._reconnect_attempt == 0

    async def test_a_failed_attempt_repaints_the_entities(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A connection lost through an event is repainted at once."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        with patch.object(coordinator, "async_update_listeners") as mock_paint:
            coordinator._set_available(False)
            mock_paint.assert_not_called()

            with patch.object(coordinator, "_schedule_reconnection"):
                coordinator._schedule_next_reconnection_if_needed()

            mock_paint.assert_called_once()

        assert coordinator.is_available is False


class TestCoordinatorDataUpdateEdgeCases:
    """Test _async_update_data edge cases."""

    async def test_update_cancelled_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test CancelledError is re-raised."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()

    async def test_update_value_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test ValueError is propagated (not a network error)."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=ValueError("Invalid data")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # ValueError should propagate (not a handled network error)
        with pytest.raises(ValueError, match="Invalid data"):
            await coordinator._async_update_data()

    async def test_update_key_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test KeyError is propagated (not a network error)."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=KeyError("Missing key")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # KeyError should propagate (not a handled network error)
        with pytest.raises(KeyError):
            await coordinator._async_update_data()

    async def test_update_type_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test TypeError is propagated (not a network error)."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=TypeError("Wrong type")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # TypeError should propagate (not a handled network error)
        with pytest.raises(TypeError):
            await coordinator._async_update_data()

    async def test_update_unexpected_exception(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test RuntimeError is propagated (not a network error)."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        # RuntimeError should propagate (not a handled network error)
        with pytest.raises(RuntimeError):
            await coordinator._async_update_data()


class TestCoordinatorSetAvailable:
    """Test _set_available method."""

    async def test_set_available_unchanged(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test set_available does nothing when unchanged."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = True
        coordinator._reconnect_attempt = 3
        coordinator._unavailable_logged = False

        # Set to same value: nothing to log, but every answer clears the count.
        coordinator._set_available(True)

        assert coordinator._last_available is True
        assert coordinator._reconnect_attempt == 0

    async def test_set_available_to_true(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test set_available to True resets counters."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = False
        coordinator._reconnect_attempt = 3

        with caplog.at_level(logging.INFO):
            coordinator._set_available(True)

        assert coordinator._last_available is True
        assert coordinator._reconnect_attempt == 0
        # First reach is not a recovery.
        assert "back online" not in caplog.text

    async def test_set_available_to_false(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test set_available to False preserves counters and logs once."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        coordinator._last_available = True
        coordinator._reconnect_attempt = 1

        with caplog.at_level(logging.INFO):
            coordinator._set_available(False)

        # State should change to unavailable
        assert coordinator._last_available is False
        # The backoff count is preserved (not reset on unavailable transition)
        assert coordinator._reconnect_attempt == 1
        assert f"Device {TEST_NODE_ID} is unavailable" in caplog.text

        caplog.clear()
        with caplog.at_level(logging.INFO):
            coordinator._set_available(True)
        assert f"Device {TEST_NODE_ID} is back online" in caplog.text


class TestAsyncUpdateDataCloudPaths:
    """Test cloud device paths in _async_update_data."""

    async def test_update_triggers_cloud_discovery(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that cloud device triggers cloud discovery path."""
        entry = create_mock_config_entry(mock_config_entry_data)

        node_config = {"devices": [{"name": "Light", "type": "esp.device.light"}]}
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_cloud_device = MagicMock(return_value=True)
        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=node_config)
        mock_esp_api.get_cloud_params = AsyncMock(return_value={})
        mock_esp_api.discover_from_cloud = AsyncMock()

        discovery_state = {"completed": False}

        def is_discovery_completed(node_id: str) -> bool:
            return discovery_state["completed"]

        def mark_discovery_completed(node_id: str) -> None:
            discovery_state["completed"] = True

        mock_esp_api.is_discovery_completed = MagicMock(
            side_effect=is_discovery_completed
        )
        mock_esp_api.mark_discovery_completed = MagicMock(
            side_effect=mark_discovery_completed
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()

        assert coordinator.is_available is True
        mock_esp_api.is_cloud_device.assert_called()
        mock_esp_api.discover_from_cloud.assert_called_once()

    async def test_update_cloud_poll_failure_marks_unavailable(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that failed cloud polling marks device unavailable."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.is_cloud_device = MagicMock(return_value=True)
        mock_esp_api.get_cloud_params = AsyncMock(return_value=None)
        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        # Force connectivity check to trigger and fail
        poller = coordinator._poller
        assert isinstance(poller, CloudPoller)
        poller._poll_count = CLOUD_CONNECTIVITY_CHECK_POLLS - 1

        await coordinator._async_update_data()

        assert coordinator.is_available is False


class TestPollingBeforeAnythingIsDiscovered:
    """Test that a coordinator with no entities yet keeps polling."""

    async def test_a_first_discovery_that_finds_nothing_is_not_the_last_one(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A fruitless discovery leaves the next poll scheduled."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_cloud_device = MagicMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=False)
        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=None)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator.async_refresh()

        assert coordinator._unsub_refresh is not None

        await coordinator.async_shutdown()

    async def test_the_stand_in_goes_once_the_entities_are_listening(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that polling follows the entities again after discovery.

        Held any longer, a device whose entities the user has disabled would go
        on being polled, which is not how any other integration behaves.
        """
        entry = create_mock_config_entry(mock_config_entry_data)

        node_config = {"devices": [{"name": "Light", "type": "esp.device.light"}]}
        discovery_state = {"completed": False}

        mock_esp_api.is_cloud_device = MagicMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(
            side_effect=lambda node_id: discovery_state["completed"]
        )
        mock_esp_api.mark_discovery_completed = MagicMock(
            side_effect=lambda node_id: discovery_state.update(completed=True)
        )
        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=node_config)
        mock_esp_api.get_cloud_params = AsyncMock(return_value={})
        mock_esp_api.discover_from_cloud = AsyncMock()
        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

        await coordinator._async_update_data()
        assert coordinator._discovery_hold is not None

        await coordinator._async_update_data()
        assert coordinator._discovery_hold is None

        await coordinator.async_shutdown()


class TestReconnectionErrorPaths:
    """Test reconnection error handling paths."""

    async def test_reconnection_cancelled_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test CancelledError is re-raised during reconnection."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        coordinator._last_available = False

        with pytest.raises(asyncio.CancelledError):
            await coordinator._attempt_single_reconnection()

    async def test_reconnection_unexpected_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test unexpected error during reconnection schedules next attempt."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False

    async def test_reconnection_network_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test network error during reconnection schedules next attempt."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_device = MagicMock(return_value=_registered_device())
        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=OSError("connection refused")
        )

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )
        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is False


class TestFirmwareVersionOnTheDevicePage:
    """What the node reports about itself, shown on its device page."""

    @staticmethod
    def _coordinator(
        hass: HomeAssistant,
        api: MagicMock,
        entry: Any,
        identity: DeviceIdentity = IDENTITY,
    ) -> ESPDataUpdateCoordinator:
        return ESPDataUpdateCoordinator(
            hass=hass,
            api=api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=identity,
        )

    async def test_the_reported_version_reaches_the_registry(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """It is parsed at discovery but nothing published it until now."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, TEST_NODE_ID)}
        )
        mock_esp_api.get_firmware_version = MagicMock(return_value="1.2.3")

        self._coordinator(hass, mock_esp_api, entry)._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert device.sw_version == "1.2.3"

    async def test_an_unchanged_version_is_not_written_again(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """This runs on every poll, so the usual case has to cost nothing."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, TEST_NODE_ID)},
            sw_version="1.2.3",
        )
        mock_esp_api.get_firmware_version = MagicMock(return_value="1.2.3")
        coordinator = self._coordinator(hass, mock_esp_api, entry)

        with patch.object(devices, "async_update_device") as update:
            coordinator._publish_reported_identity()

        update.assert_not_called()

    async def test_a_node_that_reported_no_version_is_left_alone(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Firmware is free to leave fw_version out of its config."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, TEST_NODE_ID)}
        )
        mock_esp_api.get_firmware_version = MagicMock(return_value=None)

        self._coordinator(hass, mock_esp_api, entry)._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert device.sw_version is None

    async def test_a_node_with_no_device_entry_is_left_alone(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A poll can land before the entry has registered its device."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        mock_esp_api.get_firmware_version = MagicMock(return_value="1.2.3")

        self._coordinator(hass, mock_esp_api, entry)._publish_reported_identity()

    # What the node is bound to: Acme's Bulb.
    BOUND = DeviceIdentity(
        serial_number=TEST_NODE_ID, manufacturer="acme", model="Bulb"
    )

    async def test_the_binding_and_the_node_s_own_word_both_reach_the_page(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Manufacturer and model are the product's; model id and version the node's."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, TEST_NODE_ID)}
        )
        mock_esp_api.get_reported_config = MagicMock(
            return_value=(
                {
                    "info": {
                        "model": "T1",
                        # Explicitly advertised; equality with the project name
                        # must not erase it.
                        "project_name": "T1",
                        "fw_version": "2.0",
                    }
                },
                {},
            )
        )
        mock_esp_api.get_firmware_version = MagicMock(return_value="2.0")

        self._coordinator(
            hass, mock_esp_api, entry, self.BOUND
        )._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert (device.manufacturer, device.model, device.model_id) == (
            "acme",
            "Bulb",
            "T1",
        )
        assert device.sw_version == "2.0"

    async def test_a_node_reporting_another_model_shows_it_as_the_model_id(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """After an OTA, say: the page shows what it is treated as and what it says it is."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, TEST_NODE_ID)}
        )
        mock_esp_api.get_reported_config = MagicMock(
            return_value=({"info": {"model": "T2", "project_name": "bulb_fw"}}, {})
        )

        self._coordinator(
            hass, mock_esp_api, entry, self.BOUND
        )._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert (device.manufacturer, device.model, device.model_id) == (
            "acme",
            "Bulb",
            "T2",
        )

    @pytest.mark.parametrize(
        ("info", "identity_advertised"),
        [
            ({"model": "led_light", "project_name": "led_light"}, False),
            ({"model": "*", "project_name": "led_light"}, True),
            ({"name": "node"}, True),
        ],
        ids=["not_advertised", "generic", "none"],
    )
    async def test_a_node_that_set_no_model_gets_no_model_id(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
        info: dict[str, str],
        identity_advertised: bool,
    ) -> None:
        """No advertisement or ``*`` identifies no hardware; binding fields stand."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                **mock_config_entry_data,
                CONF_IDENTITY_ADVERTISED: identity_advertised,
            },
        )
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, TEST_NODE_ID)},
            model_id="Z9",
        )
        mock_esp_api.get_reported_config = MagicMock(return_value=({"info": info}, {}))

        self._coordinator(
            hass, mock_esp_api, entry, self.BOUND
        )._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert (device.manufacturer, device.model, device.model_id) == (
            "acme",
            "Bulb",
            None,
        )

    async def test_a_rebind_rewrites_the_binding_s_fields(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Values written under another product are replaced; the node's own stay."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, TEST_NODE_ID)},
            manufacturer="zeta",
            model="Lamp",
            model_id="T1",
        )
        mock_esp_api.get_reported_config = MagicMock(
            return_value=({"info": {"model": "T1", "project_name": "bulb_fw"}}, {})
        )

        self._coordinator(
            hass, mock_esp_api, entry, self.BOUND
        )._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert (device.manufacturer, device.model, device.model_id) == (
            "acme",
            "Bulb",
            "T1",
        )

    async def test_an_unchanged_page_is_not_written_again(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Every poll compares; only a difference writes."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, TEST_NODE_ID)},
            manufacturer="acme",
            model="Bulb",
            model_id="T1",
        )
        mock_esp_api.get_reported_config = MagicMock(
            return_value=({"info": {"model": "T1", "project_name": "bulb_fw"}}, {})
        )
        coordinator = self._coordinator(hass, mock_esp_api, entry, self.BOUND)

        with patch.object(devices, "async_update_device") as update:
            coordinator._publish_reported_identity()

        update.assert_not_called()

    async def test_a_node_not_yet_read_leaves_the_page_alone(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """No config is no news: nothing is written, nothing is cleared."""
        entry = MockConfigEntry(domain=DOMAIN, data=mock_config_entry_data)
        entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, TEST_NODE_ID)},
            manufacturer="zeta",
            model_id="Z9",
        )
        mock_esp_api.get_reported_config = MagicMock(return_value=(None, None))
        mock_esp_api.get_firmware_version = MagicMock(return_value=None)

        self._coordinator(
            hass, mock_esp_api, entry, self.BOUND
        )._publish_reported_identity()

        device = devices.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
        assert device is not None
        assert (device.manufacturer, device.model_id) == ("zeta", "Z9")


class TestPollLocalDeviceState:
    """What a local read that got nothing back does to the node's standing."""

    @staticmethod
    def _coordinator(
        hass: HomeAssistant, api: MagicMock, entry: Any
    ) -> ESPDataUpdateCoordinator:
        return ESPDataUpdateCoordinator(
            hass=hass,
            api=api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

    async def test_a_poll_the_node_did_not_answer_makes_it_unavailable(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A socket that is open but no longer answers is as lost as a closed one."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.read_properties = AsyncMock(return_value=None)
        coordinator = self._coordinator(hass, mock_esp_api, entry)
        coordinator._last_available = True

        with patch.object(coordinator, "_schedule_reconnection") as schedule:
            await coordinator._async_update_data()

        assert coordinator.is_available is False
        schedule.assert_called_once()


class TestResyncStructure:
    """A write that found no parameter has the node read again, within reason."""

    @staticmethod
    def _coordinator(
        hass: HomeAssistant, api: MagicMock, entry: Any
    ) -> ESPDataUpdateCoordinator:
        return ESPDataUpdateCoordinator(
            hass=hass,
            api=api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
            identity=IDENTITY,
        )

    async def test_the_node_is_read_again(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """The first request reads the node's structure, as discovery did."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.read_properties = AsyncMock(return_value=[{"name": "config"}])
        coordinator = self._coordinator(hass, mock_esp_api, entry)

        await coordinator.async_resync_structure()

        mock_esp_api.parse_and_discover_entities.assert_awaited_once()

    async def test_a_row_of_requests_costs_one_read(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Within a minute the structure is not read again; after it, it is."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.read_properties = AsyncMock(return_value=[{"name": "config"}])
        coordinator = self._coordinator(hass, mock_esp_api, entry)

        with patch(
            "custom_components.esp_weaver.coordinator.time.monotonic",
            side_effect=[1000.0, 1030.0, 1061.0],
        ):
            await coordinator.async_resync_structure()
            await coordinator.async_resync_structure()
            assert mock_esp_api.read_properties.await_count == 1

            await coordinator.async_resync_structure()

        assert mock_esp_api.read_properties.await_count == 2
