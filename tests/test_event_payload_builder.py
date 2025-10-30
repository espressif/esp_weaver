# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the event_payload_builder module."""

import time

from custom_components.esp_weaver.iot.payload.event_payload_builder import (
    build_battery_event_payload,
    build_binary_sensor_event_payload,
    build_gesture_event_payload,
    build_input_event_payload,
    build_light_event_payload,
    build_sensor_event_payload,
    build_sleep_event_payload,
    build_threshold_event_payload,
)
from custom_components.esp_weaver.iot.specs.keys import (
    KEY_BATTERY_DATA,
    KEY_INPUT_DATA,
    KEY_LIGHT_DATA,
    KEY_NODE_ID,
    KEY_SENSOR_DATA,
    KEY_SENSOR_NAME,
    KEY_SENSOR_VALUE,
    KEY_SLEEP_DATA,
    KEY_TIMESTAMP,
    KEY_TYPE,
    KEY_VALUE,
)


class TestBuildBatteryEventPayload:
    """Test build_battery_event_payload function."""

    def test_full_battery_data(self) -> None:
        """Test with all battery fields."""
        battery_data = {
            "Battery Level": 85,
            "Voltage": 4.1,
            "Temperature": 25.5,
            "Charging Status": "charging",
            "Alert Level": 20,
        }

        result = build_battery_event_payload("test_node", battery_data)

        assert result[KEY_NODE_ID] == "test_node"
        assert KEY_TIMESTAMP in result
        assert "battery_level" in result[KEY_BATTERY_DATA]
        assert result[KEY_BATTERY_DATA]["battery_level"] == 85

    def test_partial_battery_data(self) -> None:
        """Test with partial battery fields."""
        battery_data = {"Battery Level": 50}

        result = build_battery_event_payload("test_node", battery_data)

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_BATTERY_DATA]["battery_level"] == 50
        # None values should be filtered out
        assert "voltage" not in result[KEY_BATTERY_DATA]

    def test_empty_battery_data(self) -> None:
        """Test with empty battery data."""
        result = build_battery_event_payload("test_node", {})

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_BATTERY_DATA] == {}


class TestBuildBinarySensorEventPayload:
    """Test build_binary_sensor_event_payload function."""

    def test_direct_state_field(self) -> None:
        """Test with direct State field in params."""
        params_data = {"State": True}

        result = build_binary_sensor_event_payload("test_node", params_data)

        assert result is not None
        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_SENSOR_VALUE] is True

    def test_binary_sensor_dict(self) -> None:
        """Test with Binary Sensor dict containing state."""
        params_data = {
            "Binary Sensor": {
                "State": False,
                "device_class": "motion",
            }
        }

        result = build_binary_sensor_event_payload("test_node", params_data)

        assert result is not None
        assert result[KEY_SENSOR_VALUE] is False

    def test_binary_sensor_bool(self) -> None:
        """Test with Binary Sensor as boolean."""
        params_data = {"Binary Sensor": True}

        result = build_binary_sensor_event_payload("test_node", params_data)

        assert result is not None
        assert result[KEY_SENSOR_VALUE] is True

    def test_alternative_state_keys(self) -> None:
        """Test alternative state key detection."""
        # Test 'value' key
        params_data = {"value": True}
        result = build_binary_sensor_event_payload("test_node", params_data)
        assert result is not None
        assert result[KEY_SENSOR_VALUE] is True

    def test_no_binary_sensor_data(self) -> None:
        """Test with no binary sensor data."""
        params_data = {"Light": {"Power": True}}

        result = build_binary_sensor_event_payload("test_node", params_data)

        assert result is None


class TestBuildGestureEventPayload:
    """Test build_gesture_event_payload function."""

    def test_full_gesture_data(self) -> None:
        """Test with complete gesture data."""
        gesture_data = {
            "Gesture Type": "shake",
            "Gesture Confidence": 95,
            "X Orientation": 10,
            "Y Orientation": 20,
            "Z Orientation": 30,
        }

        result = build_gesture_event_payload("test_node", gesture_data)

        assert result[KEY_NODE_ID] == "test_node"
        assert "gesture_type" in result[KEY_SENSOR_DATA]
        assert result[KEY_SENSOR_DATA]["gesture_type"] == "shake"

    def test_gesture_events(self) -> None:
        """Test gesture event fields."""
        gesture_data = {
            "Shake Event": True,
            "Push Event": False,
            "Flip Event": True,
        }

        result = build_gesture_event_payload("test_node", gesture_data)

        assert result[KEY_SENSOR_DATA]["shake_event"] is True
        assert result[KEY_SENSOR_DATA]["flip_event"] is True
        # False values should be retained in the payload
        assert result[KEY_SENSOR_DATA]["push_event"] is False

    def test_empty_gesture_data(self) -> None:
        """Test with empty gesture data."""
        result = build_gesture_event_payload("test_node", {})

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_SENSOR_DATA] == {}


class TestBuildInputEventPayload:
    """Test build_input_event_payload function."""

    def test_full_input_data(self) -> None:
        """Test with complete input data."""
        input_data = {
            "Input Type": "button",
            "Last Event": "press",
            "Input Events": ["press", "release"],
            "Input Value": 1,
            "Sensitivity": 50,
        }

        result = build_input_event_payload("test_node", input_data)

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_INPUT_DATA]["input_type"] == "button"
        assert result[KEY_INPUT_DATA]["last_event"] == "press"

    def test_empty_input_data(self) -> None:
        """Test with empty input data."""
        result = build_input_event_payload("test_node", {})

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_INPUT_DATA] == {}


class TestBuildLightEventPayload:
    """Test build_light_event_payload function."""

    def test_full_light_data(self) -> None:
        """Test with complete light data."""
        light_data = {
            "Power": True,
            "Brightness": 128,
            "Hue": 180,
            "Saturation": 50,
            "Intensity": 75,
            "Light Mode": "color",
        }

        result = build_light_event_payload("test_node", light_data)

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_LIGHT_DATA]["power"] is True
        assert result[KEY_LIGHT_DATA]["brightness"] == 128
        assert result[KEY_LIGHT_DATA]["hue"] == 180
        assert result[KEY_LIGHT_DATA]["saturation"] == 50

    def test_partial_light_data(self) -> None:
        """Test with partial light data."""
        light_data = {"Power": False, "Brightness": 50}

        result = build_light_event_payload("test_node", light_data)

        assert result[KEY_LIGHT_DATA]["power"] is False
        assert result[KEY_LIGHT_DATA]["brightness"] == 50
        # Missing fields should not be present
        assert "hue" not in result[KEY_LIGHT_DATA]

    def test_empty_light_data(self) -> None:
        """Test with empty light data."""
        result = build_light_event_payload("test_node", {})

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_LIGHT_DATA] == {}


class TestBuildSleepEventPayload:
    """Test build_sleep_event_payload function."""

    def test_full_sleep_data(self) -> None:
        """Test with complete sleep data."""
        sleep_data = {
            "Sleep State": "light_sleep",
            "Wake Reason": "timer",
            "Wake Window Status": "open",
            "Sleep Duration": 3600,
            "Wake Count": 5,
        }

        result = build_sleep_event_payload("test_node", sleep_data)

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_SLEEP_DATA]["sleep_state"] == "light_sleep"
        assert result[KEY_SLEEP_DATA]["wake_reason"] == "timer"
        assert result[KEY_SLEEP_DATA]["sleep_duration"] == 3600

    def test_empty_sleep_data(self) -> None:
        """Test with empty sleep data."""
        result = build_sleep_event_payload("test_node", {})

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_SLEEP_DATA] == {}


class TestBuildSensorEventPayload:
    """Test build_sensor_event_payload function."""

    def test_temperature_sensor(self) -> None:
        """Test temperature sensor payload."""
        result = build_sensor_event_payload("test_node", "temperature", 25.5)

        assert result[KEY_NODE_ID] == "test_node"
        assert result[KEY_SENSOR_NAME] == "temperature"
        assert result[KEY_TYPE] == "temperature"
        assert result[KEY_VALUE] == 25.5
        assert KEY_TIMESTAMP in result

    def test_humidity_sensor(self) -> None:
        """Test humidity sensor payload."""
        result = build_sensor_event_payload("test_node", "humidity", 65.0)

        assert result[KEY_SENSOR_NAME] == "humidity"
        assert result[KEY_VALUE] == 65.0

    def test_zero_value(self) -> None:
        """Test sensor with zero value."""
        result = build_sensor_event_payload("test_node", "pressure", 0.0)

        assert result[KEY_VALUE] == 0.0


class TestBuildThresholdEventPayload:
    """Test build_threshold_event_payload function."""

    def test_threshold_payload(self) -> None:
        """Test threshold event payload."""
        result = build_threshold_event_payload(
            "test_node", "temperature_min_threshold", 15.0
        )

        assert result[KEY_NODE_ID] == "test_node"
        assert result["param_name"] == "temperature_min_threshold"
        assert result[KEY_VALUE] == 15.0
        assert KEY_TIMESTAMP in result

    def test_max_threshold(self) -> None:
        """Test max threshold payload."""
        result = build_threshold_event_payload(
            "test_node", "humidity_max_threshold", 80
        )

        assert result["param_name"] == "humidity_max_threshold"
        assert result[KEY_VALUE] == 80


class TestTimestampGeneration:
    """Test timestamp generation in payloads."""

    def test_timestamp_is_reasonable(self) -> None:
        """Test timestamp is a reasonable time value."""
        before = time.time()
        result = build_sensor_event_payload("test_node", "temp", 20.0)
        after = time.time()

        assert before <= result[KEY_TIMESTAMP] <= after
