# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver low power sleep sensor platform."""

import time
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant

from custom_components.esp_weaver.iot.entity_states import SleepState
from custom_components.esp_weaver.iot.specs.events import EVENT_LOW_POWER_SLEEP_UPDATE
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_SLEEP_DATA,
    KEY_SLEEP_DURATION,
    KEY_SLEEP_STATE,
    KEY_WAKE_COUNT,
    KEY_WAKE_REASON,
    KEY_WAKE_WINDOW_STATUS,
)
from custom_components.esp_weaver.iot.specs.sleep_specs import (
    SLEEP_STATE_AWAKE,
    SLEEP_STATE_DEEP_SLEEP,
)
from custom_components.esp_weaver.low_power_sleep import (
    ESPWeaverSleepSensor,
    setup_discovery_listener,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestSleepSensorInit:
    """Test sleep sensor initialization."""

    async def test_basic_initialization(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic initialization."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        assert sensor._attr_device_class == SensorDeviceClass.ENUM
        assert isinstance(sensor._state, SleepState)
        assert sensor._state.sleep_state == SLEEP_STATE_AWAKE
        assert sensor._state.wake_reason == STATE_UNKNOWN
        assert sensor._attr_native_value == SLEEP_STATE_AWAKE

    async def test_initialization_with_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test initialization with initial data."""
        initial_data = {
            KEY_SLEEP_STATE: SLEEP_STATE_DEEP_SLEEP,  # Use valid sleep state
            KEY_WAKE_REASON: "timer",
            KEY_SLEEP_DURATION: 3600,
            KEY_WAKE_COUNT: 5,
        }

        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            initial_data=initial_data,
        )

        assert sensor._state.sleep_state == SLEEP_STATE_DEEP_SLEEP
        assert sensor._state.wake_reason == "timer"
        assert sensor._state.sleep_duration == 3600
        assert sensor._state.wake_count == 5


class TestSleepSensorProperties:
    """Test sleep sensor properties."""

    async def test_icon_property(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property for default awake state."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        icon = sensor.icon
        # Default state is "awake" which maps to "mdi:eye"
        assert icon == "mdi:eye"

    async def test_extra_state_attributes_basic(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic extra state attributes."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.wake_reason = "button"

        attrs = sensor.extra_state_attributes

        assert KEY_WAKE_REASON in attrs
        assert attrs[KEY_WAKE_REASON] == "button"

    async def test_extra_state_attributes_with_optional(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test extra state attributes with optional fields."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.wake_reason = "timer"
        sensor._state.wake_window_status = "open"
        sensor._state.sleep_duration = 7200
        sensor._state.wake_count = 10

        attrs = sensor.extra_state_attributes

        assert KEY_WAKE_WINDOW_STATUS in attrs
        assert attrs[KEY_WAKE_WINDOW_STATUS] == "open"
        assert KEY_SLEEP_DURATION in attrs
        assert attrs[KEY_SLEEP_DURATION] == 7200
        assert KEY_WAKE_COUNT in attrs
        assert attrs[KEY_WAKE_COUNT] == 10


class TestSleepSensorEventHandling:
    """Test sleep sensor event handling."""

    async def test_handle_sleep_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling sleep update event."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LOW_POWER_SLEEP_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_SLEEP_DATA: {
                    KEY_SLEEP_STATE: SLEEP_STATE_DEEP_SLEEP,  # Use valid sleep state
                    KEY_WAKE_REASON: "gpio",
                },
            },
        )

        sensor._handle_sleep_update(event)

        assert sensor._state.sleep_state == SLEEP_STATE_DEEP_SLEEP
        assert sensor._state.wake_reason == "gpio"
        sensor.async_write_ha_state.assert_called_once()

    async def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test ignoring update for different node."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LOW_POWER_SLEEP_UPDATE,
            data={
                CONF_NODE_ID: "other_node",
                KEY_SLEEP_DATA: {KEY_SLEEP_STATE: "sleeping"},
            },
        )

        sensor._handle_sleep_update(event)

        sensor.async_write_ha_state.assert_not_called()

    async def test_ignore_empty_sleep_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test ignoring empty sleep data."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LOW_POWER_SLEEP_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_SLEEP_DATA: {},
            },
        )

        sensor._handle_sleep_update(event)

        sensor.async_write_ha_state.assert_not_called()


class TestSleepSensorApplySleepData:
    """Test sleep sensor apply sleep data."""

    async def test_apply_all_fields(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test applying all sleep data fields."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        sleep_data = {
            KEY_SLEEP_STATE: "deep_sleep",
            KEY_WAKE_REASON: "rtc",
            KEY_WAKE_WINDOW_STATUS: "closed",
            KEY_SLEEP_DURATION: 1800,
            KEY_WAKE_COUNT: 3,
        }

        sensor._apply_sleep_data(sleep_data)

        assert sensor._state.sleep_state == "deep_sleep"
        assert sensor._state.wake_reason == "rtc"
        assert sensor._state.wake_window_status == "closed"
        assert sensor._state.sleep_duration == 1800
        assert sensor._state.wake_count == 3

    async def test_apply_wake_count_updates_time(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test that increasing wake count updates last wake time."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.wake_count = 5
        # Set a known old wake time to ensure deterministic comparison
        old_wake_time = time.time() - 10.0
        sensor._state.last_wake_time = old_wake_time

        # Increase wake count
        sensor._apply_sleep_data({KEY_WAKE_COUNT: 6})

        assert sensor._state.wake_count == 6
        # When wake_count increases, last_wake_time should be updated to a newer value
        assert sensor._state.last_wake_time > old_wake_time

    async def test_apply_wake_count_no_increase(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test that same wake count doesn't update time."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.wake_count = 5
        # Use float timestamp consistent with test_apply_wake_count_updates_time
        fixed_time = time.time() - 100.0
        sensor._state.last_wake_time = fixed_time

        # Same wake count
        sensor._apply_sleep_data({KEY_WAKE_COUNT: 5})

        assert sensor._state.wake_count == 5
        # Time should not change for same count
        assert sensor._state.last_wake_time == fixed_time


class TestSleepSensorLifecycle:
    """Test sleep sensor lifecycle methods."""

    async def test_async_added_to_hass(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_added_to_hass registers listener."""
        sensor = ESPWeaverSleepSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        # Use MagicMock instead of lambda for more conventional assertion
        mock_async_on_remove = MagicMock()
        sensor.async_on_remove = mock_async_on_remove

        await sensor.async_added_to_hass()

        # Should have registered 2 callbacks: coordinator listener and event bus listener
        assert mock_async_on_remove.call_count == 2
        # Verify the callback is callable
        assert callable(mock_async_on_remove.call_args[0][0])


class TestSleepDiscoveryListener:
    """Test sleep discovery listener setup."""

    async def test_setup_discovery_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test setting up discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with patch(
            "custom_components.esp_weaver.low_power_sleep.create_discovery_listener"
        ) as mock_create:
            setup_discovery_listener(hass, TEST_NODE_ID, entry)
            # Verify the correct arguments are passed (works with positional or keyword args)
            mock_create.assert_called_once()
            args, kwargs = mock_create.call_args
            assert (args[0] if args else kwargs.get("hass")) is hass
            assert (args[1] if len(args) > 1 else kwargs.get("node_id")) == TEST_NODE_ID
            assert (args[2] if len(args) > 2 else kwargs.get("config_entry")) is entry
