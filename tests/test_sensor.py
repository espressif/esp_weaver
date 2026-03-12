# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver sensor platform."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfPressure, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant

from custom_components.esp_weaver.const import ATTR_LAST_UPDATED
from custom_components.esp_weaver.iot.specs.events import (
    DOMAIN,
    EVENT_SENSOR_DISCOVERED,
    EVENT_SENSOR_UPDATE,
)
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_DEVICE_NAME,
    KEY_SENSOR_TYPE,
)
from custom_components.esp_weaver.iot.utils.sensor_utils import (
    get_sensor_threshold_config,
)
from custom_components.esp_weaver.sensor import (
    ESPWeaverSensor,
    _create_sensor_entity,
    async_setup_entry,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestSensorEntityCreation:
    """Test sensor entity creation."""

    async def test_create_temperature_sensor(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_sensor_data: dict[str, Any],
    ) -> None:
        """Test creating a temperature sensor entity."""
        sensor = _create_sensor_entity(
            event_data=mock_sensor_data,
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert sensor is not None
        assert sensor._sensor_type == "temperature"
        assert sensor._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert sensor._attr_device_class == SensorDeviceClass.TEMPERATURE
        assert sensor._attr_native_value == 25.5

    async def test_create_pressure_sensor(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating a pressure sensor entity."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            "sensor_type": "pressure",
            "initial_value": 1013.25,
            "device_info": {"name": TEST_DEVICE_NAME},
            "param": {},
        }

        sensor = _create_sensor_entity(
            event_data=event_data,
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert sensor is not None
        assert sensor._sensor_type == "pressure"
        assert sensor._attr_native_unit_of_measurement == UnitOfPressure.HPA
        # Use ATMOSPHERIC_PRESSURE to match NumberDeviceClass.ATMOSPHERIC_PRESSURE
        # for consistent unit conversion (both convert to inHg for US users)
        assert sensor._attr_device_class == SensorDeviceClass.ATMOSPHERIC_PRESSURE

    async def test_create_generic_sensor(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating a generic sensor entity."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            "sensor_type": "custom_value",
            "initial_value": 42.0,
            "unit_of_measurement": "units",
            "device_info": {"name": TEST_DEVICE_NAME},
            "param": {},
        }

        sensor = _create_sensor_entity(
            event_data=event_data,
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert sensor is not None
        assert sensor._sensor_type == "custom_value"
        assert sensor._attr_native_value == 42.0
        assert sensor._attr_native_unit_of_measurement == "units"


class TestSensorEntity:
    """Test ESPWeaverSensor entity."""

    async def test_sensor_unique_id(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor unique ID generation."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        )

        assert sensor._attr_unique_id == f"{DOMAIN}_{TEST_NODE_ID}_temperature"

    async def test_sensor_availability(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor availability property."""
        mock_coordinator.last_update_success = True
        mock_coordinator.is_available = True

        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        )

        assert sensor.available is True

        mock_coordinator.is_available = False
        assert sensor.available is False

    async def test_sensor_extra_attributes(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor extra state attributes."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.5,
        )

        attrs = sensor.extra_state_attributes
        assert ATTR_LAST_UPDATED in attrs


class TestSensorUpdateHandling:
    """Test sensor update handling."""

    async def test_handle_sensor_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sensor update event."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.5,
        )

        # Set up hass for the entity
        sensor.hass = hass
        # Mock async_write_ha_state to avoid entity registration issues
        sensor.async_write_ha_state = MagicMock()

        # Simulate sensor update event
        event = Event(
            event_type=EVENT_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "type": "temperature",
                "value": 26.0,
            },
        )

        sensor._handle_sensor_update(event)

        assert sensor._attr_native_value == 26.0
        sensor.async_write_ha_state.assert_called_once()

    async def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test update for other node is ignored."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.5,
        )

        sensor.hass = hass

        event = Event(
            event_type=EVENT_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: "different_node",
                "type": "temperature",
                "value": 30.0,
            },
        )

        sensor._handle_sensor_update(event)

        # Value should not change
        assert sensor._attr_native_value == 25.5

    async def test_ignore_update_for_different_sensor_type(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test update for different sensor type is ignored."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.5,
        )

        sensor.hass = hass

        event = Event(
            event_type=EVENT_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "type": "humidity",  # Different type
                "value": 60.0,
            },
        )

        sensor._handle_sensor_update(event)

        # Value should not change
        assert sensor._attr_native_value == 25.5


class TestSensorPlatformSetup:
    """Test sensor platform setup."""

    async def test_setup_entry_registers_discovery_listener(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that setup registers discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.sensor.setup_platform_discovery"
            ) as mock_setup,
        ):
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

            mock_setup.assert_called_once()


class TestSensorThresholdConfig:
    """Test sensor threshold configuration."""

    async def test_sensor_with_threshold_config(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor returns threshold config in attributes matching implementation."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        )

        # Get the expected threshold config from the implementation
        threshold_config = get_sensor_threshold_config("temperature")
        assert threshold_config is not None, "Temperature should have threshold config"

        # Get actual attributes from sensor
        attrs = sensor.extra_state_attributes
        assert isinstance(attrs, dict)

        # Verify that extra_state_attributes keys (minus ATTR_LAST_UPDATED)
        # match the keys from get_sensor_threshold_config
        actual_config_keys = set(attrs.keys()) - {ATTR_LAST_UPDATED}
        expected_config_keys = set(threshold_config.keys())
        assert actual_config_keys == expected_config_keys, (
            f"Attribute keys should match threshold config keys. "
            f"Got {actual_config_keys}, expected {expected_config_keys}"
        )

        # Verify each threshold value is not None and matches implementation
        for key in expected_config_keys:
            assert attrs[key] is not None, (
                f"Threshold attribute {key} should not be None"
            )
            assert attrs[key] == threshold_config[key], (
                f"Attribute {key}={attrs[key]} should equal config value {threshold_config[key]}"
            )


class TestCreateSensorEntity:
    """Test _create_sensor_entity function."""

    async def test_create_sensor_entity_basic(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating sensor entity with basic data."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "temperature",
            KEY_DEVICE_NAME: TEST_DEVICE_NAME,
            "unit": "°C",
            "value": 25.5,
        }

        sensor = _create_sensor_entity(event_data, mock_coordinator, TEST_NODE_ID)

        # Function should return a sensor entity
        assert sensor is not None
        assert sensor._sensor_type == "temperature"

    async def test_create_sensor_entity_with_unknown_type(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating sensor entity with unknown type returns entity."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "custom_sensor",
            KEY_DEVICE_NAME: TEST_DEVICE_NAME,
            "value": 100,
        }

        # Should create a generic sensor entity
        sensor = _create_sensor_entity(event_data, mock_coordinator, TEST_NODE_ID)
        assert sensor is not None

    async def test_create_sensor_entity_humidity(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating humidity sensor entity."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "humidity",
            KEY_DEVICE_NAME: TEST_DEVICE_NAME,
            "value": 65.0,
        }

        sensor = _create_sensor_entity(event_data, mock_coordinator, TEST_NODE_ID)
        assert sensor is not None
        assert sensor._sensor_type == "humidity"

    async def test_create_sensor_entity_with_param_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating sensor entity with param data."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "temperature",
            KEY_DEVICE_NAME: TEST_DEVICE_NAME,
            "param": {
                "value": 22.5,
                "unit": "°C",
            },
        }

        sensor = _create_sensor_entity(event_data, mock_coordinator, TEST_NODE_ID)
        assert sensor is not None

    async def test_create_sensor_entity_with_string_device_class(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating sensor entity with string device class gets converted."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "custom",
            KEY_DEVICE_NAME: TEST_DEVICE_NAME,
            "device_class": "temperature",  # String, not enum
        }

        sensor = _create_sensor_entity(event_data, mock_coordinator, TEST_NODE_ID)
        assert sensor is not None
        assert sensor._attr_device_class == SensorDeviceClass.TEMPERATURE

    async def test_create_sensor_entity_with_invalid_string_device_class(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating sensor entity with invalid string device class sets None."""
        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "custom",
            KEY_DEVICE_NAME: TEST_DEVICE_NAME,
            "device_class": "invalid_class_that_does_not_exist",  # Invalid string
        }

        sensor = _create_sensor_entity(event_data, mock_coordinator, TEST_NODE_ID)
        assert sensor is not None
        assert sensor._attr_device_class is None


class TestSensorPlatformSetupEdgeCases:
    """Test sensor platform setup edge cases."""

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


class TestSensorDiscoveryHandling:
    """Test sensor discovery handling."""

    async def test_discovery_handler_filters_threshold_patterns(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that threshold patterns are filtered in discovery handler."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        # Setup with mock that returns proper result
        with patch(
            "custom_components.esp_weaver.sensor.setup_platform_discovery"
        ) as mock_setup:
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

        # Simulate discovering a threshold pattern
        # The handler should filter these out
        hass.bus.async_fire(
            EVENT_SENSOR_DISCOVERED,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_SENSOR_TYPE: "temperature_min_threshold",
                "device_info": {"name": TEST_DEVICE_NAME},
            },
        )

        await hass.async_block_till_done()

        # Verify that no entities were added for threshold patterns
        async_add_entities.assert_not_called()

    async def test_discovery_handler_skips_already_discovered(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that already discovered entities are skipped."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with patch(
            "custom_components.esp_weaver.sensor.setup_platform_discovery"
        ) as mock_setup:
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            # Pre-populate with discovered entity
            mock_result.discovered_entities = {
                f"{TEST_NODE_ID}_temperature": MagicMock()
            }
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

            # Fire discovery for already discovered sensor
            hass.bus.async_fire(
                EVENT_SENSOR_DISCOVERED,
                {
                    CONF_NODE_ID: TEST_NODE_ID,
                    KEY_SENSOR_TYPE: "temperature",
                    "device_info": {"name": TEST_DEVICE_NAME},
                },
            )

            await hass.async_block_till_done()

            # async_add_entities should not be called for duplicate
            # The existing entity should remain
            assert f"{TEST_NODE_ID}_temperature" in mock_result.discovered_entities

    async def test_discovery_handler_skips_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that discovery for other nodes is skipped."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with patch(
            "custom_components.esp_weaver.sensor.setup_platform_discovery"
        ) as mock_setup:
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

            # Fire discovery for different node
            hass.bus.async_fire(
                EVENT_SENSOR_DISCOVERED,
                {
                    CONF_NODE_ID: "different_node",
                    KEY_SENSOR_TYPE: "temperature",
                    "device_info": {"name": "Other Device"},
                },
            )

            await hass.async_block_till_done()

            # Should not add entity for different node
            assert len(mock_result.discovered_entities) == 0


class TestSensorUpdateEdgeCases:
    """Test sensor update edge cases: None values, threshold violations, alert handling."""

    async def test_handle_update_with_none_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling update with None value."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.5,
        )

        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        # Event with None value
        event = Event(
            event_type=EVENT_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "type": "temperature",
                "value": None,
            },
        )

        sensor._handle_sensor_update(event)

        # Value should not change when None
        assert sensor._attr_native_value == 25.5
        sensor.async_write_ha_state.assert_not_called()

    async def test_handle_update_threshold_check(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test update triggers threshold check."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.5,
        )

        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        # Mock the threshold check method
        sensor._check_threshold_violations = AsyncMock()

        event = Event(
            event_type=EVENT_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "type": "temperature",
                "value": 30.0,  # New value triggers threshold check
            },
        )

        sensor._handle_sensor_update(event)

        # Allow any scheduled async tasks to complete
        await hass.async_block_till_done()

        # Value should be updated
        assert sensor._attr_native_value == 30.0
        sensor.async_write_ha_state.assert_called_once()
        # Threshold check should be triggered on value update
        sensor._check_threshold_violations.assert_called_once()

    async def test_sensor_type_property(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor_type property returns correct value."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="humidity",
            unit="%",
            device_class="humidity",
            state_class=SensorStateClass.MEASUREMENT,
        )

        assert sensor.sensor_type == "humidity"

    async def test_ambient_temperature_sensor_override(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test ambient temperature sensor type override."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="ambient_temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        )

        # Should override to Celsius and Temperature device class
        assert sensor._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert sensor._attr_device_class == SensorDeviceClass.TEMPERATURE


class TestSensorEdgeCases:
    """Test sensor edge cases and error handling."""

    async def test_invalid_initial_value_logs_warning(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        caplog,
    ) -> None:
        """Test invalid initial value logs warning and doesn't crash."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value="not_a_number",  # Invalid value
        )

        # Sensor should be created but value should remain None
        assert sensor._attr_native_value is None
        assert "Invalid initial value" in caplog.text

    async def test_invalid_sensor_value_in_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        caplog,
    ) -> None:
        """Test invalid sensor value in update event logs warning."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.0,
        )
        sensor.hass = hass
        sensor._attr_native_value = 25.0

        # Mock async_write_ha_state to avoid state machine issues
        sensor.async_write_ha_state = MagicMock()

        # Create event with invalid value
        event = Event(
            EVENT_SENSOR_UPDATE,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                "type": "temperature",
                "value": "invalid_value",  # Non-numeric string
            },
        )

        sensor._handle_sensor_update(event)

        # Value should remain unchanged
        assert sensor._attr_native_value == 25.0
        assert "Invalid sensor value" in caplog.text

    async def test_async_will_remove_cancels_pending_task(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_will_remove_from_hass cancels pending threshold task."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        )
        sensor.hass = hass

        # Create a real async task that we can cancel
        async def long_running_task():
            await asyncio.sleep(10)

        task = hass.async_create_task(long_running_task())
        sensor._threshold_check_task = task

        # Call async_will_remove_from_hass
        await sensor.async_will_remove_from_hass()

        # Task should have been cancelled and cleared
        assert task.cancelled() or task.done()
        assert sensor._threshold_check_task is None

    async def test_threshold_check_error_handling(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        caplog,
    ) -> None:
        """Test threshold check handles errors gracefully."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        )
        sensor.hass = hass

        # Mock alert service to raise an error
        mock_alert_service = MagicMock()
        mock_alert_service.check_and_handle_violations = AsyncMock(
            side_effect=OSError("Connection failed")
        )
        sensor._alert_service = mock_alert_service

        # Should not raise, but log error
        await sensor._check_threshold_violations(35.0, 25.0)

        assert "Error checking threshold violations" in caplog.text

    async def test_cancel_pending_threshold_task_on_new_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test that pending threshold task is cancelled when new update arrives."""
        sensor = ESPWeaverSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            initial_value=25.0,
        )
        sensor.hass = hass
        sensor._attr_native_value = 25.0

        # Create a real async task that we can cancel
        async def long_running_task():
            await asyncio.sleep(10)

        old_task = hass.async_create_task(long_running_task())
        sensor._threshold_check_task = old_task

        # Mock async_write_ha_state to avoid state machine issues
        sensor.async_write_ha_state = MagicMock()

        # Create event with new value
        event = Event(
            EVENT_SENSOR_UPDATE,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                "type": "temperature",
                "value": 30.0,
            },
        )

        sensor._handle_sensor_update(event)

        # Let the event loop process the cancellation
        await asyncio.sleep(0)

        # Old task should have been cancelled or is cancelling
        assert old_task.cancelled() or old_task.cancelling() > 0 or old_task.done()
        # New task should be stored (different from old)
        assert sensor._threshold_check_task is not None
        assert sensor._threshold_check_task != old_task
