# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver coordinator."""

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.esp_weaver.const import (
    CACHE_BINARY_SENSORS,
    CACHE_LIGHTS,
    CACHE_NUMBERS,
    CACHE_SENSORS,
    RECONNECT_DELAYS,
)
from custom_components.esp_weaver.coordinator import ESPDataUpdateCoordinator
from custom_components.esp_weaver.iot.specs.events import EVENT_CONNECTION_ERROR
from custom_components.esp_weaver.iot.specs.keys import CONF_NODE_ID

from .conftest import (
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    TEST_PORT,
    create_mock_config_entry,
)


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
        )

        assert coordinator.node_id == TEST_NODE_ID
        assert coordinator.device_name == TEST_DEVICE_NAME
        assert coordinator.is_available is True
        assert coordinator.discovery_completed is False

    async def test_coordinator_discovered_entities_init(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test discovered entities dict is properly initialized."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        assert CACHE_SENSORS in coordinator.discovered_entities
        assert CACHE_BINARY_SENSORS in coordinator.discovered_entities
        assert CACHE_LIGHTS in coordinator.discovered_entities
        assert CACHE_NUMBERS in coordinator.discovered_entities


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
        )

        # Simulate first discovery completed
        coordinator._discovery_completed = True

        data = await coordinator._async_update_data()

        # CoordinatorData is a TypedDict, so use dict access
        assert data["node_id"] == TEST_NODE_ID
        assert data["available"] is True

    async def test_update_data_device_offline(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test data update when device is offline."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        data = await coordinator._async_update_data()

        # CoordinatorData is a TypedDict, so use dict access
        assert data["available"] is False
        assert coordinator.is_available is False

    async def test_update_data_network_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test data update handles network errors with graceful degradation."""
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
        )

        # First failure should return data with graceful degradation (keeps last available state)
        data = await coordinator._async_update_data()
        assert coordinator._consecutive_failures == 1
        assert data is not None
        assert data["node_id"] == TEST_NODE_ID
        # Graceful degradation keeps last available state (initially True)
        assert data["available"] is True

        # Second failure should also return data with graceful degradation
        data = await coordinator._async_update_data()
        assert coordinator._consecutive_failures == 2
        assert data is not None
        assert data["node_id"] == TEST_NODE_ID
        # Still keeps last available state
        assert data["available"] is True

        # Third failure should raise UpdateFailed
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_update_data_timeout_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test data update handles timeout errors."""
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
        )

        # Should handle timeout gracefully (keeps last available state)
        data = await coordinator._async_update_data()
        assert coordinator._consecutive_failures == 1
        assert data["node_id"] == TEST_NODE_ID
        # Graceful degradation keeps last available state (initially True)
        assert data["available"] is True


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

        mock_client = MagicMock()
        mock_client.get_property_values = AsyncMock(return_value=mock_device_properties)

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
        mock_esp_api.registry = MagicMock()
        mock_esp_api.registry.get_client = MagicMock(return_value=mock_client)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._async_update_data()

        # Discovery should be triggered
        mock_esp_api.parse_and_discover_entities.assert_called_once()
        mock_esp_api.mark_discovery_completed.assert_called_once_with(TEST_NODE_ID)
        assert coordinator.discovery_completed is True

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
        )
        coordinator._discovery_completed = True

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
        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
        mock_esp_api.is_mdns_available = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
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
        )

        # Simulate device becoming unavailable
        coordinator._last_available = False
        coordinator._reconnect_in_progress = False

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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        # Simulate connection error event
        event = Event(
            event_type=EVENT_CONNECTION_ERROR,
            data={CONF_NODE_ID: TEST_NODE_ID},
        )

        coordinator._handle_connection_error(event)

        assert coordinator.is_available is False

    async def test_connection_error_ignored_for_other_device(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test connection error for other device is ignored."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        # Simulate connection error for different device
        event = Event(
            event_type=EVENT_CONNECTION_ERROR,
            data={CONF_NODE_ID: "different_node"},
        )

        # Should remain available
        original_available = coordinator.is_available
        coordinator._handle_connection_error(event)
        assert coordinator.is_available == original_available

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
        )

        # Set reconnect in progress
        coordinator._reconnect_in_progress = True
        coordinator._last_available = True

        event = Event(
            event_type=EVENT_CONNECTION_ERROR,
            data={CONF_NODE_ID: TEST_NODE_ID},
        )

        coordinator._handle_connection_error(event)

        # Should still be available (reconnect in progress)
        assert coordinator._last_available is True


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
        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        coordinator._last_available = False

        # Run delayed reconnection with mocked sleep to avoid CI flakiness
        # Note: asyncio.sleep is mocked, so the delay value is irrelevant;
        # using RECONNECT_DELAYS[0] here for documentation purposes
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coordinator._delayed_reconnection(RECONNECT_DELAYS[0])

        # Task reference should be cleared (in finally block)
        assert coordinator._reconnect_task is None

    async def test_attempt_reconnection_already_in_progress(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection returns False when already in progress."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        coordinator._reconnect_in_progress = True

        result = await coordinator._attempt_single_reconnection()

        assert result is False

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
        )

        coordinator._last_available = True
        coordinator._reconnect_in_progress = False

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

        mock_esp_api.devices = {}  # No device info

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
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

        mock_esp_api.devices = {TEST_NODE_ID: {"port": TEST_PORT}}  # No IP

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
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
        )

        coordinator._last_available = False

        result = await coordinator._attempt_single_reconnection()

        assert result is True
        mock_esp_api.is_mdns_available.assert_called_once()
        mock_esp_api.register_device.assert_called_once()

    async def test_attempt_reconnection_mdns_fails(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test reconnection fails when mDNS fails."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        mock_esp_api.is_mdns_available = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
        mock_esp_api.default_port = TEST_PORT
        mock_esp_api.is_device_available_async = AsyncMock(side_effect=TimeoutError())

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
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
        )

        # Set attempt to max
        coordinator._reconnect_attempt = len(RECONNECT_DELAYS)

        with patch.object(coordinator, "_schedule_reconnection") as mock_schedule:
            coordinator._schedule_next_reconnection_if_needed()

            # Should not schedule, should reset
            mock_schedule.assert_not_called()
            assert coordinator._reconnect_attempt == 0


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
        )

        # RuntimeError should propagate (not a handled network error)
        with pytest.raises(RuntimeError):
            await coordinator._async_update_data()


class TestCoordinatorFetchDeviceProperties:
    """Test _fetch_device_properties method."""

    async def test_fetch_properties_no_client(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test fetch returns None when no client."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.registry = MagicMock()
        mock_esp_api.registry.get_client.return_value = None

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        result = await coordinator._fetch_device_properties()

        assert result is None

    async def test_fetch_properties_success(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful property fetch."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_client = MagicMock()
        mock_client.get_property_values = AsyncMock(return_value=[{"name": "test"}])
        mock_esp_api.registry = MagicMock()
        mock_esp_api.registry.get_client.return_value = mock_client

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        result = await coordinator._fetch_device_properties()

        assert result == [{"name": "test"}]


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
        )

        coordinator._last_available = True
        coordinator._consecutive_failures = 5
        coordinator._reconnect_attempt = 3

        # Set to same value
        coordinator._set_available(True)

        # Counters should NOT be reset (no change)
        assert coordinator._consecutive_failures == 5
        assert coordinator._reconnect_attempt == 3

    async def test_set_available_to_true(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test set_available to True resets counters."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        coordinator._last_available = False
        coordinator._consecutive_failures = 5
        coordinator._reconnect_attempt = 3

        coordinator._set_available(True)

        assert coordinator._last_available is True
        assert coordinator._consecutive_failures == 0
        assert coordinator._reconnect_attempt == 0

    async def test_set_available_to_false(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test set_available to False preserves counters."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        coordinator._last_available = True
        coordinator._consecutive_failures = 2
        coordinator._reconnect_attempt = 1

        coordinator._set_available(False)

        # State should change to unavailable
        assert coordinator._last_available is False
        # Counters should be preserved (not reset on unavailable transition)
        assert coordinator._consecutive_failures == 2
        assert coordinator._reconnect_attempt == 1
