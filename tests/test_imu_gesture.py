# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver IMU gesture sensor platform."""

import asyncio
import contextlib
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.const import STATE_OFF
from homeassistant.core import Event, HomeAssistant

from custom_components.esp_weaver.const import (
    ATTR_ORIENTATION_X,
    ATTR_ORIENTATION_Y,
    ATTR_ORIENTATION_Z,
)
from custom_components.esp_weaver.imu_gesture import (
    ESPWeaverGestureSensor,
    setup_discovery_listener,
)
from custom_components.esp_weaver.iot.entity_states import GestureState
from custom_components.esp_weaver.iot.specs.events import EVENT_IMU_GESTURE_UPDATE
from custom_components.esp_weaver.iot.specs.gesture_specs import GESTURE_IDLE
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_CONFIDENCE,
    KEY_GESTURE,
    KEY_POWER,
    KEY_SENSITIVITY,
    KEY_SENSOR_DATA,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry

# Test duration constants for timer tests
SHORT_DURATION = 0.01  # Very short for fast test completion
LONG_DURATION = 10.0  # Long enough to test cancellation


class TestGestureSensorInit:
    """Test gesture sensor initialization."""

    async def test_basic_initialization(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic initialization."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        assert isinstance(sensor._state, GestureState)
        assert sensor._reset_timer is None
        assert isinstance(sensor._gesture_events, dict)

    async def test_initialization_with_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test initialization with initial data."""
        # Use correct field names from gesture_specs
        initial_data = {
            "gesture_type": "shake",  # FIELD_GESTURE_TYPE
            "gesture_confidence": 85,  # FIELD_GESTURE_CONFIDENCE
            "power": True,  # FIELD_POWER
            "sensitivity": 5,  # FIELD_SENSITIVITY
        }

        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            initial_data=initial_data,
        )

        assert sensor._state is not None
        assert sensor._state.gesture == "shake"
        assert sensor._state.confidence == 85
        assert sensor._state.power is True
        assert sensor._state.sensitivity == 5


class TestGestureSensorProperties:
    """Test gesture sensor properties."""

    async def test_native_value_power_off(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test native value when power off."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.power = False

        assert sensor.native_value == STATE_OFF

    async def test_native_value_with_gesture(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test native value with gesture."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.power = True
        sensor._state.gesture = GESTURE_IDLE

        value = sensor.native_value
        assert value == "Idle"  # GESTURE_IDLE maps to "Idle" display value

    async def test_icon_power_off(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon when power off."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.power = False

        assert sensor.icon == "mdi:power-off"

    async def test_icon_with_gesture(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon with gesture."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.power = True
        sensor._state.gesture = "tap"

        icon = sensor.icon
        # "tap" is not in GESTURE_ICONS mapping, so it falls back to
        # DEFAULT_GESTURE_ICON ("mdi:gesture-tap"), not a generated icon
        assert icon == "mdi:gesture-tap"

    async def test_extra_state_attributes(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test extra state attributes contain correct values."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.gesture = "tap"
        sensor._state.confidence = 90
        sensor._state.power = True
        sensor._state.sensitivity = 5
        sensor._state.orientation_x = 10.0
        sensor._state.orientation_y = 20.0
        sensor._state.orientation_z = 30.0

        attrs = sensor.extra_state_attributes

        # Assert keys are present AND values match the sensor internal state
        assert attrs[KEY_GESTURE] == "tap"
        assert attrs[KEY_CONFIDENCE] == 90
        assert attrs[KEY_POWER] is True
        assert attrs[KEY_SENSITIVITY] == 5
        assert attrs[ATTR_ORIENTATION_X] == 10.0
        assert attrs[ATTR_ORIENTATION_Y] == 20.0
        assert attrs[ATTR_ORIENTATION_Z] == 30.0


class TestGestureSensorEventHandling:
    """Test gesture sensor event handling."""

    async def test_handle_gesture_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling gesture update event."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_IMU_GESTURE_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_SENSOR_DATA: {
                    "gesture": "shake",
                    "confidence": 80,
                },
            },
        )

        sensor._handle_gesture_update(event)

        sensor.async_write_ha_state.assert_called_once()

    async def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test ignoring update for different node."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_IMU_GESTURE_UPDATE,
            data={
                CONF_NODE_ID: "other_node",
                KEY_SENSOR_DATA: {"gesture": "tap"},
            },
        )

        sensor._handle_gesture_update(event)

        sensor.async_write_ha_state.assert_not_called()


class TestGestureSensorTimer:
    """Test gesture sensor timer functionality."""

    async def test_cancel_reset_timer_no_timer(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test canceling timer when no timer exists."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._reset_timer = None

        # Should not raise
        sensor._cancel_reset_timer()
        assert sensor._reset_timer is None

    async def test_cancel_reset_timer_with_timer(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test canceling existing timer."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        # Create a mock task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        sensor._reset_timer = mock_task

        sensor._cancel_reset_timer()

        mock_task.cancel.assert_called_once()
        assert sensor._reset_timer is None

    async def test_auto_reset_to_idle(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test auto reset to idle state."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor._state.display_duration = SHORT_DURATION
        sensor._state.gesture = "tap"
        # Simulate device that reports confidence
        sensor._state.confidence = 85
        sensor.async_write_ha_state = MagicMock()

        # Create task and set _reset_timer so the condition check passes
        task = asyncio.create_task(sensor._auto_reset_to_idle())
        sensor._reset_timer = task
        await task

        assert sensor._state.gesture == GESTURE_IDLE
        # Confidence resets to 0 when device supports it
        assert sensor._state.confidence == 0
        sensor.async_write_ha_state.assert_called_once()

    async def test_auto_reset_cancelled(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test auto reset when cancelled."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor._state.display_duration = LONG_DURATION
        sensor._state.gesture = "tap"
        sensor.async_write_ha_state = MagicMock()

        # Start the task
        task = asyncio.create_task(sensor._auto_reset_to_idle())
        await asyncio.sleep(0.01)
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        # State should not have changed
        assert sensor._state.gesture == "tap"


class TestGestureSensorLifecycle:
    """Test gesture sensor lifecycle methods."""

    async def test_async_added_to_hass(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_added_to_hass registers listener."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        remove_callbacks = []
        sensor.async_on_remove = lambda cb: remove_callbacks.append(cb)

        await sensor.async_added_to_hass()

        # Should have registered a callback
        assert len(remove_callbacks) >= 1

    async def test_async_will_remove_from_hass(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_will_remove_from_hass cancels timer."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        mock_task = MagicMock()
        mock_task.done.return_value = False
        sensor._reset_timer = mock_task

        await sensor.async_will_remove_from_hass()

        mock_task.cancel.assert_called_once()


class TestGestureDiscoveryListener:
    """Test gesture discovery listener setup."""

    async def test_setup_discovery_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test setting up discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with patch(
            "custom_components.esp_weaver.imu_gesture.create_discovery_listener"
        ) as mock_create:
            setup_discovery_listener(hass, TEST_NODE_ID, entry)
            mock_create.assert_called_once()
            # Verify positional arguments passed to create_discovery_listener
            call_args = mock_create.call_args
            assert call_args[0][0] is hass
            assert call_args[0][1] == TEST_NODE_ID
            assert call_args[0][2] is entry


class TestApplyGestureResult:
    """Test applying gesture result."""

    async def test_apply_gesture_result(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test applying gesture processing result."""
        sensor = ESPWeaverGestureSensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        # Create mock result with orientation keys matching KEY_ORIENTATION_X/Y/Z
        mock_result = MagicMock()
        mock_result.gesture = "double_tap"
        mock_result.confidence = 95
        mock_result.events = {"tap": True}
        mock_result.display_duration = 2.0
        mock_result.power = True
        mock_result.sensitivity = 7
        mock_result.orientation = {
            "x": 1.0,  # KEY_ORIENTATION_X
            "y": 2.0,  # KEY_ORIENTATION_Y
            "z": 3.0,  # KEY_ORIENTATION_Z
            "change": 0.5,  # KEY_ORIENTATION_CHANGE
        }

        sensor._apply_gesture_result(mock_result)

        assert sensor._state.gesture == "double_tap"
        assert sensor._state.confidence == 95
        assert sensor._state.power is True
        assert sensor._state.sensitivity == 7
        assert sensor._state.orientation_x == 1.0
        assert sensor._state.orientation_y == 2.0
        assert sensor._state.orientation_z == 3.0
        assert sensor._state.display_duration == 2.0
        assert sensor._gesture_events == {"tap": True}
