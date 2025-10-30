# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver number platform (threshold controls)."""

import asyncio
import contextlib
import datetime
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.number import NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.esp_weaver.const import ATTR_LAST_UPDATED, CACHE_NUMBERS
from custom_components.esp_weaver.helpers.threshold_manager import ThresholdManager
from custom_components.esp_weaver.iot.specs.events import (
    DOMAIN,
    EVENT_SENSOR_DISCOVERED,
    EVENT_THRESHOLD_DATA_RECEIVED,
    EVENT_THRESHOLD_UPDATE_TO_DEVICE,
)
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_PARAM_NAME,
    KEY_SENSOR_TYPE,
    KEY_VALUE,
    THRESHOLD_TYPE_MAX,
    THRESHOLD_TYPE_MIN,
)
import custom_components.esp_weaver.number as number_module
from custom_components.esp_weaver.number import (
    ESPWeaverThresholdNumber,
    _handle_sensor_discovered,
    async_setup_entry,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestThresholdNumberEntity:
    """Test ESPWeaverThresholdNumber entity."""

    async def test_threshold_number_initialization_min(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test min threshold number entity initialization."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )

        expected_unique_id = f"{DOMAIN}_{TEST_NODE_ID}_temperature_min_threshold"
        assert number._attr_unique_id == expected_unique_id
        assert number._attr_mode == NumberMode.BOX
        assert number._attr_native_value == 10.0
        # Entity name excludes device name when has_entity_name=True
        assert number._attr_name == "Temperature Min Threshold"

    async def test_threshold_number_initialization_max(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test max threshold number entity initialization."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MAX,
            initial_value=30.0,
        )

        expected_unique_id = f"{DOMAIN}_{TEST_NODE_ID}_temperature_max_threshold"
        assert number._attr_unique_id == expected_unique_id
        # Entity name excludes device name when has_entity_name=True
        assert number._attr_name == "Temperature Max Threshold"
        assert number._attr_native_value == 30.0

    async def test_threshold_number_default_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test threshold number uses default when no initial value."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=None,
        )

        # Should have a default value from the range config
        assert number._attr_native_value is not None

    async def test_threshold_number_properties(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test threshold number properties."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
        )

        assert number.sensor_type == "temperature"
        assert number.threshold_type == THRESHOLD_TYPE_MIN


class TestThresholdNumberSetValue:
    """Test threshold number value setting."""

    async def test_set_native_value_success(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test setting threshold value successfully."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )

        # Use mock hass to avoid read-only attribute issue
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_hass.bus.async_fire = MagicMock()
        number.hass = mock_hass
        number.async_write_ha_state = MagicMock()

        await number.async_set_native_value(15.0)

        assert number._attr_native_value == 15.0
        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert call_args[0][0] == EVENT_THRESHOLD_UPDATE_TO_DEVICE
        assert call_args[0][1][CONF_NODE_ID] == TEST_NODE_ID
        # Verify HA state was written
        number.async_write_ha_state.assert_called()

    async def test_set_native_value_out_of_range_low(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test setting value below minimum - HA validates via service, entity accepts any value."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        # Use mock hass to avoid entity_id requirement
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_hass.bus.async_fire = MagicMock()
        number.hass = mock_hass
        number.async_write_ha_state = MagicMock()

        # Entity accepts value - HA services handle validation
        await number.async_set_native_value(number._attr_native_min_value - 100)

        # Value is set (HA service layer would normally validate)
        assert number._attr_native_value == number._attr_native_min_value - 100

    async def test_set_native_value_out_of_range_high(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test setting value above maximum - HA validates via service, entity accepts any value."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MAX,
            initial_value=30.0,
        )
        # Use mock hass to avoid entity_id requirement
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_hass.bus.async_fire = MagicMock()
        number.hass = mock_hass
        number.async_write_ha_state = MagicMock()

        # Entity accepts value - HA service layer would normally validate
        await number.async_set_native_value(number._attr_native_max_value + 100)

        # Value is set (HA service layer would normally validate)
        assert number._attr_native_value == number._attr_native_max_value + 100


class TestThresholdNumberDeviceUpdate:
    """Test threshold number device update handling."""

    async def test_set_device_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test setting value from device (no sync back)."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )

        # Use mock hass to avoid read-only attribute issue
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_hass.bus.async_fire = MagicMock()
        number.hass = mock_hass
        number.async_write_ha_state = MagicMock()

        await number.async_set_device_value(20.0)

        assert number._attr_native_value == 20.0
        # Should NOT fire event back to device
        mock_hass.bus.async_fire.assert_not_called()
        # But should still write HA state
        number.async_write_ha_state.assert_called_once()

    async def test_threshold_entity_attributes(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test threshold entity has correct sensor and threshold type attributes."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass

        # Verify internal attributes are set correctly
        assert number._sensor_type == "temperature"
        assert number._threshold_type == THRESHOLD_TYPE_MIN


class TestThresholdNumberExtraAttributes:
    """Test threshold number extra attributes."""

    async def test_extra_attributes_with_last_sync(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test extra attributes include last sync time."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
        )
        number.hass = hass

        # Initially no last sync
        attrs = number.extra_state_attributes
        assert attrs.get(ATTR_LAST_UPDATED) is None

        # Simulate sync with timezone-aware UTC datetime (Home Assistant convention)
        number._last_device_sync = datetime.datetime.now(datetime.UTC)
        attrs = number.extra_state_attributes
        assert ATTR_LAST_UPDATED in attrs


class TestThresholdNumberPlatformSetup:
    """Test number platform setup."""

    async def test_setup_entry_registers_listener(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that setup registers sensor discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.number.setup_platform_discovery"
            ) as mock_setup,
            patch(
                "custom_components.esp_weaver.number.ThresholdManager"
            ) as mock_threshold_manager,
        ):
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.coordinator.api = MagicMock()
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            mock_manager = MagicMock()
            mock_manager.setup_listeners = MagicMock(return_value=[])
            mock_manager.replay_discovered_sensors = MagicMock()
            mock_threshold_manager.return_value = mock_manager

            await async_setup_entry(hass, entry, async_add_entities)

            mock_setup.assert_called_once()
            mock_threshold_manager.assert_called_once()


class TestThresholdNumberSensorTypes:
    """Test threshold number for different sensor types."""

    @pytest.mark.parametrize(
        "sensor_type",
        [
            "temperature",
            "pressure",
            "humidity",
        ],
    )
    async def test_threshold_for_sensor_type(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        sensor_type: str,
    ) -> None:
        """Test threshold entity for various sensor types."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type=sensor_type,
            threshold_type=THRESHOLD_TYPE_MIN,
        )

        assert number._sensor_type == sensor_type
        # Should have valid min/max values
        assert number._attr_native_min_value is not None
        assert number._attr_native_max_value is not None
        assert number._attr_native_min_value < number._attr_native_max_value


class TestHandleSensorDiscovered:
    """Test _handle_sensor_discovered function."""

    async def test_handle_sensor_discovered_no_node_id(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling discovery with no node_id."""
        mock_event = MagicMock()
        mock_event.data = {}

        mock_manager = MagicMock(spec=ThresholdManager)
        mock_manager.extract_discovery_event_data.return_value = (None, None, None)

        async_add = MagicMock()
        discovered = {}

        await _handle_sensor_discovered(
            event=mock_event,
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered,
            async_add_entities=async_add,
            coordinator=mock_coordinator,
            threshold_manager=mock_manager,
        )

        async_add.assert_not_called()

    async def test_handle_sensor_discovered_wrong_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling discovery for wrong node."""
        mock_event = MagicMock()
        mock_event.data = {}

        mock_manager = MagicMock(spec=ThresholdManager)
        mock_manager.extract_discovery_event_data.return_value = (
            "other_node",
            "temp",
            "Device",
        )

        async_add = MagicMock()
        discovered = {}

        await _handle_sensor_discovered(
            event=mock_event,
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered,
            async_add_entities=async_add,
            coordinator=mock_coordinator,
            threshold_manager=mock_manager,
        )

        async_add.assert_not_called()

    async def test_handle_sensor_discovered_no_sensor_type(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling discovery with no sensor type."""
        mock_event = MagicMock()
        mock_event.data = {}

        mock_manager = MagicMock(spec=ThresholdManager)
        mock_manager.extract_discovery_event_data.return_value = (
            TEST_NODE_ID,
            None,
            "Device",
        )

        async_add = MagicMock()
        discovered = {}

        await _handle_sensor_discovered(
            event=mock_event,
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered,
            async_add_entities=async_add,
            coordinator=mock_coordinator,
            threshold_manager=mock_manager,
        )

        async_add.assert_not_called()

    async def test_handle_sensor_discovered_creates_entities(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling discovery creates threshold entities."""
        mock_event = MagicMock()
        mock_event.data = {"threshold_values": {"min": 10.0, "max": 30.0}}

        mock_manager = MagicMock(spec=ThresholdManager)
        mock_manager.extract_discovery_event_data.return_value = (
            TEST_NODE_ID,
            "temperature",
            "Device",
        )

        async_add = MagicMock()
        discovered = {}

        await _handle_sensor_discovered(
            event=mock_event,
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered,
            async_add_entities=async_add,
            coordinator=mock_coordinator,
            threshold_manager=mock_manager,
        )

        # Should have called async_add_entities with 2 entities (min and max)
        async_add.assert_called_once()
        entities = async_add.call_args[0][0]
        assert len(entities) == 2

        # Verify we have one min and one max threshold entity
        threshold_types = {e.threshold_type for e in entities}
        assert threshold_types == {THRESHOLD_TYPE_MIN, THRESHOLD_TYPE_MAX}

        # Verify sensor type
        assert all(e.sensor_type == "temperature" for e in entities)

    async def test_handle_sensor_discovered_skips_existing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling discovery skips existing entities."""
        mock_event = MagicMock()
        mock_event.data = {"threshold_values": {}}

        mock_manager = MagicMock(spec=ThresholdManager)
        mock_manager.extract_discovery_event_data.return_value = (
            TEST_NODE_ID,
            "temperature",
            "Device",
        )

        async_add = MagicMock()
        # Pre-populate with existing entities
        discovered = {
            f"{TEST_NODE_ID}_temperature_min_threshold": MagicMock(),
            f"{TEST_NODE_ID}_temperature_max_threshold": MagicMock(),
        }

        await _handle_sensor_discovered(
            event=mock_event,
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered,
            async_add_entities=async_add,
            coordinator=mock_coordinator,
            threshold_manager=mock_manager,
        )

        # Should not add any new entities
        async_add.assert_not_called()


class TestThresholdNumberListenerSetup:
    """Test threshold listener setup."""

    async def test_async_added_to_hass(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity added to hass sets up listeners."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass

        # Track async_on_remove calls
        remove_callbacks = []
        number.async_on_remove = lambda cb: remove_callbacks.append(cb)

        # Call setup which registers listeners
        number._setup_threshold_listeners()

        # Should have registered a listener (via async_on_remove)
        assert len(remove_callbacks) == 1


class TestThresholdNumberSetup:
    """Test threshold number setup - covering lines 71-72."""

    async def test_setup_entry_returns_early_without_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test setup returns early when no coordinator."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None

        async_add_entities = MagicMock()

        # Should return without error
        await async_setup_entry(hass, entry, async_add_entities)

        # async_add_entities should not be called
        async_add_entities.assert_not_called()


class TestThresholdListenerHandling:
    """Test threshold listener handling - covering lines 265-274."""

    async def test_handle_device_threshold_update_different_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling threshold update for different node."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire event for different node
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: "different_node",
                KEY_PARAM_NAME: "temp_min_threshold",
                KEY_VALUE: 15.0,
            },
        )
        await hass.async_block_till_done()

        # Value should not change
        assert number._attr_native_value == 10.0

    async def test_handle_device_threshold_update_matching(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling matching threshold update from device."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()
        number.async_write_ha_state = MagicMock()

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire event with matching param name
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "temp_min_threshold",
                KEY_VALUE: 15.0,
            },
        )
        await hass.async_block_till_done()

        # Verify the value was updated
        assert number._attr_native_value == 15.0
        # Verify async_write_ha_state was called
        assert number.async_write_ha_state.called

    async def test_handle_device_threshold_update_wrong_param(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling threshold update with wrong param name."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire event with wrong param name
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "humidity_max_threshold",  # Wrong type
                KEY_VALUE: 80.0,
            },
        )
        await hass.async_block_till_done()

        # Value should not change
        assert number._attr_native_value == 10.0


class TestThresholdNumberAsyncAddedToHass:
    """Test async_added_to_hass - covering lines 256-257."""

    async def test_async_added_to_hass_calls_setup(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_added_to_hass sets up threshold listeners."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass

        # Mock setup method at class level
        with patch.object(
            ESPWeaverThresholdNumber, "_setup_threshold_listeners"
        ) as mock_setup:
            await number.async_added_to_hass()

            mock_setup.assert_called_once()


class TestThresholdListenerEdgeCases:
    """Test edge cases in threshold listener handling."""

    async def test_handle_device_threshold_update_none_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling threshold update with None value."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()
        number.async_write_ha_state = MagicMock()

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire event with None value
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "temp_min_threshold",
                KEY_VALUE: None,
            },
        )
        await hass.async_block_till_done()

        # Value should not change
        assert number._attr_native_value == 10.0

    async def test_handle_device_threshold_update_invalid_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling threshold update with invalid (non-numeric) value."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire event with invalid value
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "temp_min_threshold",
                KEY_VALUE: "invalid_string",
            },
        )
        await hass.async_block_till_done()

        # Value should not change due to ValueError
        assert number._attr_native_value == 10.0

    async def test_handle_device_threshold_update_out_of_range(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling threshold update with value out of range."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire event with value out of range (below min or above max)
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "temp_min_threshold",
                KEY_VALUE: -999.0,  # Way below normal range
            },
        )
        await hass.async_block_till_done()

        # Value should not change due to range validation
        assert number._attr_native_value == 10.0


class TestNumberPlatformSetupNoResult:
    """Test number platform setup when no result is returned."""

    async def test_setup_entry_returns_early_when_no_result(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test setup returns early when setup_platform_discovery returns None."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.number.setup_platform_discovery"
            ) as mock_setup,
            patch(
                "custom_components.esp_weaver.number.ThresholdManager"
            ) as mock_threshold_manager,
        ):
            mock_setup.return_value = None

            await async_setup_entry(hass, entry, async_add_entities)

            mock_setup.assert_called_once()
            # ThresholdManager should not be instantiated when result is None
            mock_threshold_manager.assert_not_called()


class TestHandleSensorDiscoveredEdgeCases:
    """Test edge cases in _handle_sensor_discovered function."""

    async def test_handle_sensor_discovered_no_event_node_id(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sensor discovery with missing node_id."""
        mock_event_data = {
            # No node_id
            KEY_SENSOR_TYPE: "temperature",
            "device_name": TEST_DEVICE_NAME,
        }

        mock_threshold_manager = MagicMock(spec=ThresholdManager)
        mock_threshold_manager.extract_discovery_event_data = MagicMock(
            return_value=(None, "temperature", TEST_DEVICE_NAME)
        )

        discovered_entities: dict[str, Any] = {}
        async_add_entities = MagicMock()

        await _handle_sensor_discovered(
            event=MagicMock(data=mock_event_data),
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
            coordinator=mock_coordinator,
            threshold_manager=mock_threshold_manager,
        )

        # No entities should be added
        async_add_entities.assert_not_called()

    async def test_handle_sensor_discovered_different_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sensor discovery for different node."""
        mock_threshold_manager = MagicMock(spec=ThresholdManager)
        mock_threshold_manager.extract_discovery_event_data = MagicMock(
            return_value=("different_node", "temperature", TEST_DEVICE_NAME)
        )

        discovered_entities: dict[str, Any] = {}
        async_add_entities = MagicMock()

        await _handle_sensor_discovered(
            event=MagicMock(data={}),
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
            coordinator=mock_coordinator,
            threshold_manager=mock_threshold_manager,
        )

        # No entities should be added for different node
        async_add_entities.assert_not_called()

    async def test_handle_sensor_discovered_no_sensor_type(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sensor discovery with missing sensor_type."""
        mock_threshold_manager = MagicMock(spec=ThresholdManager)
        mock_threshold_manager.extract_discovery_event_data = MagicMock(
            return_value=(TEST_NODE_ID, None, TEST_DEVICE_NAME)
        )

        discovered_entities: dict[str, Any] = {}
        async_add_entities = MagicMock()

        await _handle_sensor_discovered(
            event=MagicMock(data={}),
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
            coordinator=mock_coordinator,
            threshold_manager=mock_threshold_manager,
        )

        # No entities should be added
        async_add_entities.assert_not_called()

    async def test_handle_sensor_discovered_already_discovered(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sensor discovery for already discovered entity."""
        mock_threshold_manager = MagicMock(spec=ThresholdManager)
        mock_threshold_manager.extract_discovery_event_data = MagicMock(
            return_value=(TEST_NODE_ID, "temperature", TEST_DEVICE_NAME)
        )

        # Pre-populate discovered entities
        discovered_entities: dict[str, Any] = {
            f"{TEST_NODE_ID}_temperature_min_threshold": MagicMock(),
            f"{TEST_NODE_ID}_temperature_max_threshold": MagicMock(),
        }
        async_add_entities = MagicMock()

        await _handle_sensor_discovered(
            event=MagicMock(data={}),
            node_id=TEST_NODE_ID,
            api=MagicMock(),
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
            coordinator=mock_coordinator,
            threshold_manager=mock_threshold_manager,
        )

        # No new entities should be added
        async_add_entities.assert_not_called()


class TestThresholdValidation:
    """Test threshold min/max validation (lines 236-253)."""

    async def test_set_min_threshold_above_max_raises_error(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test setting min threshold >= max threshold raises ServiceValidationError."""
        # Create min threshold entity
        min_number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )

        # Create max threshold entity
        max_number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MAX,
            initial_value=30.0,
        )

        # Register max entity in coordinator's discovered entities
        mock_coordinator.discovered_entities = {
            CACHE_NUMBERS: {
                f"{TEST_NODE_ID}_temperature_max_threshold": max_number,
            }
        }

        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        min_number.hass = mock_hass

        # Try to set min value >= max value (should raise)
        with pytest.raises(ServiceValidationError):
            await min_number.async_set_native_value(35.0)

    async def test_set_max_threshold_below_min_raises_error(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test setting max threshold <= min threshold raises ServiceValidationError."""
        # Create min threshold entity
        min_number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=20.0,
        )

        # Create max threshold entity
        max_number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MAX,
            initial_value=30.0,
        )

        # Register min entity in coordinator's discovered entities
        mock_coordinator.discovered_entities = {
            CACHE_NUMBERS: {
                f"{TEST_NODE_ID}_temperature_min_threshold": min_number,
            }
        }

        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        max_number.hass = mock_hass

        # Try to set max value <= min value (should raise)
        with pytest.raises(ServiceValidationError):
            await max_number.async_set_native_value(15.0)


class TestPairedThresholdValue:
    """Test _get_paired_threshold_value (line 279)."""

    async def test_get_paired_value_not_found(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test _get_paired_threshold_value returns None when paired entity not found."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )

        # Empty discovered entities
        mock_coordinator.discovered_entities = {CACHE_NUMBERS: {}}

        result = number._get_paired_threshold_value()
        assert result is None

    async def test_get_paired_value_no_cache(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test _get_paired_threshold_value returns None when no cache exists."""
        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )

        # No CACHE_NUMBERS key
        mock_coordinator.discovered_entities = {}

        result = number._get_paired_threshold_value()
        assert result is None


class TestTaskExceptionCallbacks:
    """Test task exception callbacks (lines 93-112, 357-365)."""

    async def test_handle_sensor_discovered_task_exception(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test that task exceptions are logged properly."""
        caplog.set_level(logging.ERROR)
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.number.setup_platform_discovery"
            ) as mock_setup,
            patch(
                "custom_components.esp_weaver.number.ThresholdManager"
            ) as mock_threshold_manager,
        ):
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.coordinator.api = MagicMock()
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            mock_manager = MagicMock()
            mock_manager.setup_listeners = MagicMock(return_value=[])
            mock_manager.replay_discovered_sensors = MagicMock()
            # Make extract_discovery_event_data raise an exception
            mock_manager.extract_discovery_event_data = MagicMock(
                side_effect=ValueError("Test extraction error")
            )
            mock_threshold_manager.return_value = mock_manager

            await async_setup_entry(hass, entry, async_add_entities)

            # Fire the sensor discovery event to trigger the handler
            hass.bus.async_fire(
                EVENT_SENSOR_DISCOVERED,
                {
                    CONF_NODE_ID: TEST_NODE_ID,
                    KEY_SENSOR_TYPE: "temperature",
                },
            )
            await hass.async_block_till_done()

            # Verify the error was logged
            assert any(
                "Error handling sensor discovery" in record.message
                for record in caplog.records
            )

    async def test_threshold_update_task_exception_callback(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test log_threshold_error callback logs exceptions."""
        caplog.set_level(logging.ERROR)

        number = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type=THRESHOLD_TYPE_MIN,
            initial_value=10.0,
        )
        number.hass = hass
        number.async_on_remove = MagicMock()

        # Make async_set_device_value raise an exception
        async def failing_set_device_value(value: float) -> None:
            raise ValueError("Test device value error")

        number.async_set_device_value = failing_set_device_value  # type: ignore[method-assign]

        # Setup listeners
        number._setup_threshold_listeners()

        # Fire an event that will trigger the callback and cause the exception
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "temp_min_threshold",
                KEY_VALUE: 15.0,
            },
        )
        await hass.async_block_till_done()

        # Verify the exception was logged
        assert any(
            "Error updating threshold value" in record.message
            for record in caplog.records
        )

    async def test_log_task_exception_cancelled_no_error(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test that cancelled tasks don't produce error logs when using callback."""
        caplog.set_level(logging.ERROR)

        # Create a callback that mirrors the log_task_exception pattern in number.py
        def log_task_exception(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            if exc := t.exception():
                number_module._LOGGER.exception(
                    "Error handling sensor discovery", exc_info=exc
                )

        # Create a task that gets cancelled
        async def cancellable_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(cancellable_task())
        task.add_done_callback(log_task_exception)
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Verify task was cancelled and no errors were logged
        assert task.cancelled()
        # Cancelled tasks should not produce error log messages via the callback
        assert not any("Error" in record.message for record in caplog.records)


class TestHandleSensorDiscoveredNoApi:
    """Test _handle_sensor_discovered when api is None."""

    async def test_handle_sensor_discovered_no_api(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sensor discovery with no api returns early."""
        mock_threshold_manager = MagicMock(spec=ThresholdManager)
        mock_threshold_manager.extract_discovery_event_data = MagicMock(
            return_value=(TEST_NODE_ID, "temperature", TEST_DEVICE_NAME)
        )

        discovered_entities: dict[str, Any] = {}
        async_add_entities = MagicMock()

        await _handle_sensor_discovered(
            event=MagicMock(data={}),
            node_id=TEST_NODE_ID,
            api=None,  # No API
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
            coordinator=mock_coordinator,
            threshold_manager=mock_threshold_manager,
        )

        # No entities should be added
        async_add_entities.assert_not_called()


class TestPressureThresholdImperialUnits:
    """Test pressure threshold with imperial units (inHg)."""

    async def test_handle_device_threshold_update_pressure_hpa_to_inhg(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test pressure threshold update converts hPa to inHg before range check.

        This test verifies the fix for the bug where device reports pressure in hPa
        but the UI range is in inHg - the conversion must happen BEFORE range check.
        """
        # Mock imperial unit system during entity creation
        with patch(
            "custom_components.esp_weaver.number.is_imperial_unit_system",
            return_value=True,
        ):
            number = ESPWeaverThresholdNumber(
                coordinator=mock_coordinator,
                node_id=TEST_NODE_ID,
                device_name=TEST_DEVICE_NAME,
                sensor_type="pressure",
                threshold_type=THRESHOLD_TYPE_MAX,
                initial_value=1050.0,  # Will be converted to ~31.0 inHg
            )

        number.hass = hass
        number.async_on_remove = MagicMock()
        number.async_write_ha_state = MagicMock()

        # Verify entity is configured for imperial units
        assert number._use_inhg is True
        # Range should be in inHg (~29.53 to ~32.48)
        assert number._attr_native_min_value is not None
        assert 29 < number._attr_native_min_value < 30

        # Setup listeners
        number._setup_threshold_listeners()

        # Device sends 1030 hPa which should convert to ~30.42 inHg
        # The UI range for max pressure is approximately [29.53, 32.48] inHg
        device_value_hpa = 1030.0
        expected_inhg = 1030.0 * 0.02953  # ~30.42 inHg

        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "pressure_max_threshold",
                KEY_VALUE: device_value_hpa,
            },
        )
        await hass.async_block_till_done()

        # Value should be set in inHg (converted from hPa)
        assert number._attr_native_value is not None
        assert abs(number._attr_native_value - expected_inhg) < 0.1

    async def test_handle_device_threshold_update_pressure_out_of_range_after_conversion(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test pressure value rejected when out of range AFTER conversion to inHg."""
        # Mock imperial unit system during entity creation
        with patch(
            "custom_components.esp_weaver.number.is_imperial_unit_system",
            return_value=True,
        ):
            number = ESPWeaverThresholdNumber(
                coordinator=mock_coordinator,
                node_id=TEST_NODE_ID,
                device_name=TEST_DEVICE_NAME,
                sensor_type="pressure",
                threshold_type=THRESHOLD_TYPE_MAX,
                initial_value=1050.0,
            )

        number.hass = hass
        number.async_on_remove = MagicMock()
        number.async_write_ha_state = MagicMock()

        # Verify entity is configured for imperial units
        assert number._use_inhg is True

        initial_value = number._attr_native_value

        # Setup listeners
        number._setup_threshold_listeners()

        # Device sends 1200 hPa which converts to ~35.4 inHg
        # This is above the max range of ~32.48 inHg
        hass.bus.async_fire(
            EVENT_THRESHOLD_DATA_RECEIVED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_PARAM_NAME: "pressure_max_threshold",
                KEY_VALUE: 1200.0,  # ~35.4 inHg, out of range
            },
        )
        await hass.async_block_till_done()

        # Value should NOT change due to out-of-range after conversion
        assert number._attr_native_value == initial_value
