# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver interactive input sensor platform."""

from typing import Any
from unittest.mock import ANY, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import Event, HomeAssistant

from custom_components.esp_weaver.interactive_input import (
    ESPWeaverInputSensor,
    setup_discovery_listener,
)
from custom_components.esp_weaver.iot.entity_states import InputState
from custom_components.esp_weaver.iot.specs.events import EVENT_INTERACTIVE_INPUT_UPDATE
from custom_components.esp_weaver.iot.specs.input_specs import (
    INPUT_EVENT_NONE,
    INPUT_TYPE_BUTTON,
)
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_INPUT_CONFIG,
    KEY_INPUT_DATA,
    KEY_INPUT_EVENTS,
    KEY_INPUT_MAPPING,
    KEY_INPUT_TYPE,
    KEY_INPUT_VALUE,
    KEY_LAST_EVENT,
    KEY_SENSITIVITY,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestInputSensorInit:
    """Test input sensor initialization."""

    async def test_basic_initialization(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic initialization."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        assert sensor._attr_device_class == SensorDeviceClass.ENUM
        assert isinstance(sensor._state, InputState)
        assert sensor._state.input_type == INPUT_TYPE_BUTTON
        assert sensor._state.last_event == INPUT_EVENT_NONE
        assert sensor._attr_native_value == INPUT_EVENT_NONE

    async def test_initialization_with_data(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test initialization with initial data."""
        initial_data = {
            KEY_INPUT_TYPE: "encoder",
            KEY_LAST_EVENT: "increment",  # Use valid event from _attr_options
            KEY_INPUT_VALUE: 10,
            KEY_SENSITIVITY: 5,
        }

        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            initial_data=initial_data,
        )

        assert sensor._state is not None
        assert sensor._state.input_type == "encoder"
        assert sensor._state.last_event == "increment"
        assert sensor._state.input_value == 10
        assert sensor._state.sensitivity == 5


class TestInputSensorProperties:
    """Test input sensor properties."""

    async def test_icon_property(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.input_type = INPUT_TYPE_BUTTON
        sensor._state.last_event = "click"

        icon = sensor.icon
        # Verify the specific icon for button click event
        assert icon == "mdi:gesture-tap"

    async def test_extra_state_attributes_basic(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic extra state attributes."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.input_type = INPUT_TYPE_BUTTON
        sensor._state.last_event = "click"

        attrs = sensor.extra_state_attributes

        assert KEY_INPUT_TYPE in attrs
        assert attrs[KEY_INPUT_TYPE] == INPUT_TYPE_BUTTON
        assert KEY_INPUT_EVENTS in attrs
        assert attrs[KEY_INPUT_EVENTS] == "click"

    async def test_extra_state_attributes_with_optional(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test extra state attributes with optional fields."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.input_type = "encoder"
        sensor._state.last_event = "rotate"
        sensor._state.input_value = 50
        sensor._state.sensitivity = 8
        sensor._state.input_config = {"mode": "absolute"}
        sensor._state.input_mapping = {"cw": "volume_up", "ccw": "volume_down"}

        attrs = sensor.extra_state_attributes

        assert KEY_INPUT_VALUE in attrs
        assert attrs[KEY_INPUT_VALUE] == 50
        assert KEY_SENSITIVITY in attrs
        assert attrs[KEY_SENSITIVITY] == 8
        assert KEY_INPUT_CONFIG in attrs
        assert KEY_INPUT_MAPPING in attrs


class TestInputSensorEventHandling:
    """Test input sensor event handling."""

    async def test_handle_input_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling input update event."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_INTERACTIVE_INPUT_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_INPUT_DATA: {
                    KEY_LAST_EVENT: "double_click",
                    KEY_INPUT_VALUE: 100,
                },
            },
        )

        sensor._handle_input_update(event)

        sensor.async_write_ha_state.assert_called_once()
        assert sensor._state.last_event == "double_click"
        assert sensor._state.input_value == 100
        assert sensor._attr_native_value == "double_click"

    async def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test ignoring update for different node."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_INTERACTIVE_INPUT_UPDATE,
            data={
                CONF_NODE_ID: "other_node",
                KEY_INPUT_DATA: {KEY_LAST_EVENT: "press"},
            },
        )

        sensor._handle_input_update(event)

        sensor.async_write_ha_state.assert_not_called()


class TestInputSensorApplyUpdates:
    """Test input sensor apply updates."""

    async def test_apply_empty_updates(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test applying empty updates."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        original_type = sensor._state.input_type

        sensor._apply_updates({})

        assert sensor._state.input_type == original_type

    async def test_apply_all_updates(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test applying all update fields."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        updates = {
            KEY_INPUT_TYPE: "slider",
            KEY_LAST_EVENT: "swipe",  # Use valid event from _attr_options
            KEY_INPUT_VALUE: 75,
            KEY_SENSITIVITY: 10,
            KEY_INPUT_CONFIG: {"range": [0, 100]},
            KEY_INPUT_MAPPING: {"min": 0, "max": 100},
        }

        sensor._apply_updates(updates)

        assert sensor._state.input_type == "slider"
        assert sensor._state.last_event == "swipe"
        assert sensor._attr_native_value == "swipe"
        assert sensor._state.input_value == 75
        assert sensor._state.sensitivity == 10
        assert sensor._state.input_config == {"range": [0, 100]}
        assert sensor._state.input_mapping == {"min": 0, "max": 100}


class TestInputSensorLifecycle:
    """Test input sensor lifecycle methods."""

    async def test_async_added_to_hass(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_added_to_hass registers listener."""
        sensor = ESPWeaverInputSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_on_remove = MagicMock()

        await sensor.async_added_to_hass()

        # Should have registered 2 callbacks: coordinator listener and event bus listener
        assert sensor.async_on_remove.call_count == 2


class TestInputDiscoveryListener:
    """Test input discovery listener setup."""

    async def test_setup_discovery_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test setting up discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with patch(
            "custom_components.esp_weaver.interactive_input.create_discovery_listener"
        ) as mock_create:
            setup_discovery_listener(hass, TEST_NODE_ID, entry)
            mock_create.assert_called_once_with(hass, TEST_NODE_ID, entry, ANY)
