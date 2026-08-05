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
    CLOUD_CONNECTIVITY_CHECK_POLLS,
    CLOUD_PARAMS_FAILURE_THRESHOLD,
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
    """Test _discover_local_device method."""

    async def test_fetch_properties_no_client(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test discover exits early when no client."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.registry = MagicMock()
        mock_esp_api.registry.get_client.return_value = None
        mock_esp_api.parse_and_discover_entities = AsyncMock()

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._discover_local_device()

        mock_esp_api.parse_and_discover_entities.assert_not_called()

    async def test_fetch_properties_success(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful local device discovery."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_client = MagicMock()
        mock_client.get_property_values = AsyncMock(return_value=[{"name": "test"}])
        mock_esp_api.registry = MagicMock()
        mock_esp_api.registry.get_client.return_value = mock_client
        mock_esp_api.parse_and_discover_entities = AsyncMock()
        mock_esp_api.mark_discovery_completed = MagicMock()

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._discover_local_device()

        mock_esp_api.parse_and_discover_entities.assert_called_once_with(
            TEST_NODE_ID,
            [{"name": "test"}],
            preferred_device_name=TEST_DEVICE_NAME,
        )
        mock_esp_api.mark_discovery_completed.assert_called_once_with(TEST_NODE_ID)


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


class TestDiscoverCloudDevice:
    """Test _discover_cloud_device method."""

    async def test_discover_cloud_device_success(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful cloud device discovery."""
        entry = create_mock_config_entry(mock_config_entry_data)

        node_config = {"devices": [{"name": "Light", "type": "esp.device.light"}]}
        cloud_params = {"Light": {"power": True}}
        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=node_config)
        mock_esp_api.get_cloud_params = AsyncMock(return_value=cloud_params)
        mock_esp_api.discover_from_cloud = AsyncMock()
        mock_esp_api.mark_discovery_completed = MagicMock()

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._discover_cloud_device()

        mock_esp_api.discover_from_cloud.assert_called_once_with(
            TEST_NODE_ID, node_config, cloud_params, TEST_DEVICE_NAME
        )
        mock_esp_api.mark_discovery_completed.assert_called_once_with(TEST_NODE_ID)

    async def test_discover_cloud_device_no_config(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test cloud discovery returns early when no config."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=None)
        mock_esp_api.discover_from_cloud = AsyncMock()

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._discover_cloud_device()

        mock_esp_api.discover_from_cloud.assert_not_called()

    async def test_discover_cloud_device_no_devices_key(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test cloud discovery returns early when config has no 'devices' key."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_cloud_node_config = AsyncMock(return_value={"info": {}})
        mock_esp_api.discover_from_cloud = AsyncMock()

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._discover_cloud_device()

        mock_esp_api.discover_from_cloud.assert_not_called()


class TestPollCloudDeviceState:
    """Test _poll_cloud_device_state method."""

    async def test_poll_success_with_params(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful cloud polling with params."""
        entry = create_mock_config_entry(mock_config_entry_data)

        params = {"Light": {"power": True}}
        mock_esp_api.get_cloud_params = AsyncMock(return_value=params)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        with patch(
            "custom_components.esp_weaver.coordinator.fire_property_events"
        ) as mock_fire:
            result = await coordinator._poll_cloud_device_state()

        assert result is True
        mock_fire.assert_called_once_with(hass, TEST_NODE_ID, params)
        assert coordinator._cloud_params_failures == 0

    async def test_poll_no_params_under_threshold(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test cloud polling with no params stays available under threshold."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_cloud_params = AsyncMock(return_value=None)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        result = await coordinator._poll_cloud_device_state()

        assert result is True
        assert coordinator._cloud_params_failures == 1

    async def test_poll_no_params_exceeds_threshold(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test cloud polling marks unavailable after threshold failures."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.get_cloud_params = AsyncMock(return_value=None)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )
        coordinator._cloud_params_failures = CLOUD_PARAMS_FAILURE_THRESHOLD - 1

        result = await coordinator._poll_cloud_device_state()

        assert result is False

    async def test_poll_device_offline_returns_false(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test poll returns False when device is marked offline."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )
        coordinator._cloud_device_offline = True

        result = await coordinator._poll_cloud_device_state()

        assert result is False

    async def test_poll_triggers_connectivity_check(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test connectivity check triggered at poll interval."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )
        coordinator._cloud_poll_count = CLOUD_CONNECTIVITY_CHECK_POLLS - 1

        result = await coordinator._poll_cloud_device_state()

        assert result is False
        assert coordinator._cloud_poll_count == 0


class TestCheckCloudConnectivity:
    """Test _check_cloud_connectivity method."""

    async def test_device_connected(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test device reported as connected."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=True)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )
        coordinator._cloud_device_offline = True

        result = await coordinator._check_cloud_connectivity()

        assert result is True
        assert coordinator._cloud_device_offline is False

    async def test_device_disconnected(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test device reported as disconnected."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=False)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        result = await coordinator._check_cloud_connectivity()

        assert result is False
        assert coordinator._cloud_device_offline is True

    async def test_device_not_found(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test device not found in cloud account."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=None)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        result = await coordinator._check_cloud_connectivity()

        assert result is False
        assert coordinator._cloud_device_offline is True


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
        )

        result = await coordinator._async_update_data()

        assert result["available"] is True
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
        )
        # Force connectivity check to trigger and fail
        coordinator._cloud_poll_count = CLOUD_CONNECTIVITY_CHECK_POLLS - 1

        result = await coordinator._async_update_data()

        assert result["available"] is False


class TestDiscoverLocalDeviceEdgeCases:
    """Test _discover_local_device edge cases."""

    async def test_discover_local_empty_properties(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test discover exits early when properties are empty."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_client = MagicMock()
        mock_client.get_property_values = AsyncMock(return_value=[])
        mock_esp_api.registry = MagicMock()
        mock_esp_api.registry.get_client.return_value = mock_client
        mock_esp_api.parse_and_discover_entities = AsyncMock()

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        await coordinator._discover_local_device()

        mock_esp_api.parse_and_discover_entities.assert_not_called()


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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
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

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=RuntimeError("unexpected")
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

        assert result is False
        assert coordinator._reconnect_in_progress is False

    async def test_reconnection_network_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test network error during reconnection schedules next attempt."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST, "port": TEST_PORT}}
        mock_esp_api.is_device_available_async = AsyncMock(
            side_effect=OSError("connection refused")
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

        assert result is False

    async def test_refresh_after_reconnect_handles_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test _refresh_after_reconnect handles errors gracefully."""
        entry = create_mock_config_entry(mock_config_entry_data)

        coordinator = ESPDataUpdateCoordinator(
            hass=hass,
            api=mock_esp_api,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            config_entry=entry,
        )

        with patch.object(
            coordinator, "async_refresh", side_effect=UpdateFailed("fail")
        ):
            await coordinator._refresh_after_reconnect()
