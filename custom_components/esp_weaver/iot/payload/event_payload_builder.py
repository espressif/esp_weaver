# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Event payload builders (ESP → HA).

This module builds event payloads for Home Assistant internal event dispatching.
Used when ESP devices report property updates to Home Assistant.
Only non-None values are included in event payloads.

Note: This differs from command_builder.py which builds commands (HA → ESP).
"""

import time
from typing import Any

from ..specs.device_specs import (
    DEVICE_KEY_STATE,
    DEVICE_TYPE_BINARY_SENSOR,
    ESP_PROP_ALERT_LEVEL,
    ESP_PROP_BATTERY_LEVEL,
    ESP_PROP_CHARGING_STATUS,
    ESP_PROP_CIRCLE_EVENT,
    ESP_PROP_CLAP_DOUBLE_EVENT,
    ESP_PROP_CLAP_SINGLE_EVENT,
    ESP_PROP_CLAP_TRIPLE_EVENT,
    ESP_PROP_FLIP_EVENT,
    ESP_PROP_GESTURE_CONFIDENCE,
    ESP_PROP_GESTURE_TYPE,
    ESP_PROP_INPUT_CONFIG,
    ESP_PROP_INPUT_EVENTS,
    ESP_PROP_INPUT_MAPPING,
    ESP_PROP_INPUT_TYPE,
    ESP_PROP_INPUT_VALUE,
    ESP_PROP_LAST_EVENT,
    ESP_PROP_ORIENTATION_CHANGE,
    ESP_PROP_POWER,
    ESP_PROP_PUSH_EVENT,
    ESP_PROP_ROTATION_EVENT,
    ESP_PROP_SENSITIVITY,
    ESP_PROP_SHAKE_EVENT,
    ESP_PROP_SLEEP_DURATION,
    ESP_PROP_SLEEP_STATE,
    ESP_PROP_TEMPERATURE,
    ESP_PROP_TOSS_EVENT,
    ESP_PROP_VOLTAGE,
    ESP_PROP_WAKE_COUNT,
    ESP_PROP_WAKE_REASON,
    ESP_PROP_WAKE_WINDOW_STATUS,
    ESP_PROP_X_ORIENTATION,
    ESP_PROP_Y_ORIENTATION,
    ESP_PROP_Z_ORIENTATION,
)
from ..specs.keys import (
    KEY_ALERT_LEVEL,
    KEY_BATTERY_DATA,
    KEY_BATTERY_LEVEL,
    KEY_BRIGHTNESS,
    KEY_CHARGING_STATUS,
    KEY_CIRCLE_EVENT,
    KEY_CLAP_DOUBLE_EVENT,
    KEY_CLAP_SINGLE_EVENT,
    KEY_CLAP_TRIPLE_EVENT,
    KEY_DEBOUNCE_TIME,
    KEY_DEVICE_CLASS,
    KEY_FLIP_EVENT,
    KEY_GESTURE_CONFIDENCE,
    KEY_GESTURE_TYPE,
    KEY_HUE,
    KEY_INPUT_CONFIG,
    KEY_INPUT_DATA,
    KEY_INPUT_EVENTS,
    KEY_INPUT_MAPPING,
    KEY_INPUT_TYPE,
    KEY_INPUT_VALUE,
    KEY_INTENSITY,
    KEY_LAST_EVENT,
    KEY_LIGHT_DATA,
    KEY_LIGHT_MODE,
    KEY_NODE_ID,
    KEY_ORIENTATION_CHANGE,
    KEY_PARAM_NAME,
    KEY_PARAMS,
    KEY_POWER,
    KEY_PUSH_EVENT,
    KEY_REPORT_INTERVAL,
    KEY_ROTATION_EVENT,
    KEY_SATURATION,
    KEY_SENSITIVITY,
    KEY_SENSOR_DATA,
    KEY_SENSOR_NAME,
    KEY_SENSOR_VALUE,
    KEY_SHAKE_EVENT,
    KEY_SLEEP_DATA,
    KEY_SLEEP_DURATION,
    KEY_SLEEP_STATE,
    KEY_STATE,
    KEY_TEMPERATURE,
    KEY_TIMESTAMP,
    KEY_TOSS_EVENT,
    KEY_TYPE,
    KEY_VALUE,
    KEY_VOLTAGE,
    KEY_WAKE_COUNT,
    KEY_WAKE_REASON,
    KEY_WAKE_WINDOW_STATUS,
    KEY_X_ORIENTATION,
    KEY_Y_ORIENTATION,
    KEY_Z_ORIENTATION,
)
from ..specs.light_specs import (
    ESP_PROP_BRIGHTNESS,
    ESP_PROP_HUE,
    ESP_PROP_INTENSITY,
    ESP_PROP_LIGHT_MODE,
    ESP_PROP_SATURATION,
)

# Alternative state keys for binary sensor extraction
_ALTERNATIVE_STATE_KEYS = ("state", "value", "sensor_value", "Status")


def _filter_none(data: dict[str, Any]) -> dict[str, Any]:
    """Filter out None values from dictionary."""
    return {k: v for k, v in data.items() if v is not None}


# Event Data Builders


def build_battery_event_payload(
    node_id: str, battery_data: dict[str, Any]
) -> dict[str, Any]:
    """Build battery update event data."""
    return {
        KEY_NODE_ID: node_id,
        KEY_BATTERY_DATA: _filter_none(
            {
                KEY_BATTERY_LEVEL: battery_data.get(ESP_PROP_BATTERY_LEVEL),
                KEY_VOLTAGE: battery_data.get(ESP_PROP_VOLTAGE),
                KEY_TEMPERATURE: battery_data.get(ESP_PROP_TEMPERATURE),
                KEY_CHARGING_STATUS: battery_data.get(ESP_PROP_CHARGING_STATUS),
                KEY_ALERT_LEVEL: battery_data.get(ESP_PROP_ALERT_LEVEL),
            }
        ),
        KEY_TIMESTAMP: time.time(),
    }


def _extract_binary_sensor_data(
    params_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract binary sensor data from params_data.

    Args:
        params_data: Raw params data from device.

    Returns:
        Extracted binary sensor data dict, or None if no valid data found.
    """
    # Direct State field
    if DEVICE_KEY_STATE in params_data:
        return {KEY_STATE: bool(params_data[DEVICE_KEY_STATE])}

    # Binary Sensor data
    bs_data = params_data.get(DEVICE_TYPE_BINARY_SENSOR)
    if bs_data is not None:
        if isinstance(bs_data, (bool, int)):
            return {KEY_STATE: bool(bs_data)}

        if isinstance(bs_data, dict):
            state = bs_data.get(DEVICE_KEY_STATE)
            if state is None:
                for alt_key in _ALTERNATIVE_STATE_KEYS:
                    state = bs_data.get(alt_key)
                    if state is not None:
                        break

            # Return data even without state (for device_class updates)
            result: dict[str, Any] = {
                KEY_DEVICE_CLASS: bs_data.get(KEY_DEVICE_CLASS),
                KEY_DEBOUNCE_TIME: bs_data.get(KEY_DEBOUNCE_TIME),
                KEY_REPORT_INTERVAL: bs_data.get(KEY_REPORT_INTERVAL),
            }
            if state is not None:
                result[KEY_STATE] = bool(state)
            # Return None if no meaningful data extracted
            if any(v is not None for v in result.values()):
                return result
            return None

    # Alternative lowercase keys at root level
    for alt_key in _ALTERNATIVE_STATE_KEYS:
        if alt_key in params_data:
            value = params_data[alt_key]
            if isinstance(value, (bool, int)):
                return {KEY_STATE: bool(value)}

    return None


def build_binary_sensor_event_payload(
    node_id: str,
    params_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build binary sensor update event data.

    Args:
        node_id: Device node ID.
        params_data: Raw params data from device.

    Returns:
        Event payload dict, or None if no valid binary sensor data found.
    """
    bs_data = _extract_binary_sensor_data(params_data)
    if bs_data is None:
        return None

    event_data: dict[str, Any] = {
        KEY_NODE_ID: node_id,
        KEY_TIMESTAMP: time.time(),
    }

    state = bs_data.get(KEY_STATE)
    if state is not None:
        event_data[KEY_SENSOR_VALUE] = bool(state)

    params = _filter_none(
        {
            KEY_DEVICE_CLASS: bs_data.get(KEY_DEVICE_CLASS),
            KEY_DEBOUNCE_TIME: bs_data.get(KEY_DEBOUNCE_TIME),
            KEY_REPORT_INTERVAL: bs_data.get(KEY_REPORT_INTERVAL),
        }
    )
    if params:
        event_data[KEY_PARAMS] = params

    return event_data


def build_gesture_event_payload(
    node_id: str, gesture_data: dict[str, Any]
) -> dict[str, Any]:
    """Build IMU gesture update event data."""
    return {
        KEY_NODE_ID: node_id,
        KEY_SENSOR_DATA: _filter_none(
            {
                KEY_GESTURE_TYPE: gesture_data.get(ESP_PROP_GESTURE_TYPE),
                KEY_GESTURE_CONFIDENCE: gesture_data.get(ESP_PROP_GESTURE_CONFIDENCE),
                KEY_X_ORIENTATION: gesture_data.get(ESP_PROP_X_ORIENTATION),
                KEY_Y_ORIENTATION: gesture_data.get(ESP_PROP_Y_ORIENTATION),
                KEY_Z_ORIENTATION: gesture_data.get(ESP_PROP_Z_ORIENTATION),
                KEY_ORIENTATION_CHANGE: gesture_data.get(ESP_PROP_ORIENTATION_CHANGE),
                KEY_POWER: gesture_data.get(ESP_PROP_POWER),
                KEY_SHAKE_EVENT: gesture_data.get(ESP_PROP_SHAKE_EVENT),
                KEY_PUSH_EVENT: gesture_data.get(ESP_PROP_PUSH_EVENT),
                KEY_CIRCLE_EVENT: gesture_data.get(ESP_PROP_CIRCLE_EVENT),
                KEY_FLIP_EVENT: gesture_data.get(ESP_PROP_FLIP_EVENT),
                KEY_TOSS_EVENT: gesture_data.get(ESP_PROP_TOSS_EVENT),
                KEY_ROTATION_EVENT: gesture_data.get(ESP_PROP_ROTATION_EVENT),
                KEY_CLAP_SINGLE_EVENT: gesture_data.get(ESP_PROP_CLAP_SINGLE_EVENT),
                KEY_CLAP_DOUBLE_EVENT: gesture_data.get(ESP_PROP_CLAP_DOUBLE_EVENT),
                KEY_CLAP_TRIPLE_EVENT: gesture_data.get(ESP_PROP_CLAP_TRIPLE_EVENT),
                KEY_SENSITIVITY: gesture_data.get(ESP_PROP_SENSITIVITY),
            }
        ),
        KEY_TIMESTAMP: time.time(),
    }


def build_input_event_payload(
    node_id: str, input_data: dict[str, Any]
) -> dict[str, Any]:
    """Build interactive input update event data."""
    return {
        KEY_NODE_ID: node_id,
        KEY_INPUT_DATA: _filter_none(
            {
                KEY_INPUT_TYPE: input_data.get(ESP_PROP_INPUT_TYPE),
                KEY_LAST_EVENT: input_data.get(ESP_PROP_LAST_EVENT),
                KEY_INPUT_EVENTS: input_data.get(ESP_PROP_INPUT_EVENTS),
                KEY_INPUT_VALUE: input_data.get(ESP_PROP_INPUT_VALUE),
                KEY_INPUT_CONFIG: input_data.get(ESP_PROP_INPUT_CONFIG),
                KEY_INPUT_MAPPING: input_data.get(ESP_PROP_INPUT_MAPPING),
                KEY_SENSITIVITY: input_data.get(ESP_PROP_SENSITIVITY),
            }
        ),
        KEY_TIMESTAMP: time.time(),
    }


def build_light_event_payload(
    node_id: str, light_data: dict[str, Any]
) -> dict[str, Any]:
    """Build light update event data."""
    return {
        KEY_NODE_ID: node_id,
        KEY_LIGHT_DATA: _filter_none(
            {
                KEY_POWER: light_data.get(ESP_PROP_POWER),
                KEY_BRIGHTNESS: light_data.get(ESP_PROP_BRIGHTNESS),
                KEY_HUE: light_data.get(ESP_PROP_HUE),
                KEY_SATURATION: light_data.get(ESP_PROP_SATURATION),
                KEY_INTENSITY: light_data.get(ESP_PROP_INTENSITY),
                KEY_LIGHT_MODE: light_data.get(ESP_PROP_LIGHT_MODE),
            }
        ),
        KEY_TIMESTAMP: time.time(),
    }


def build_sensor_event_payload(
    node_id: str, sensor_type: str, value: float
) -> dict[str, Any]:
    """Build sensor update event data.

    Note: Both sensor_name and type are set to sensor_type for backward
    compatibility with different event consumers.
    """
    return {
        KEY_NODE_ID: node_id,
        KEY_SENSOR_NAME: sensor_type,
        KEY_TYPE: sensor_type,
        KEY_VALUE: value,
        KEY_TIMESTAMP: time.time(),
    }


def build_sleep_event_payload(
    node_id: str, sleep_data: dict[str, Any]
) -> dict[str, Any]:
    """Build low power sleep update event data."""
    return {
        KEY_NODE_ID: node_id,
        KEY_SLEEP_DATA: _filter_none(
            {
                KEY_SLEEP_STATE: sleep_data.get(ESP_PROP_SLEEP_STATE),
                KEY_WAKE_REASON: sleep_data.get(ESP_PROP_WAKE_REASON),
                KEY_WAKE_WINDOW_STATUS: sleep_data.get(ESP_PROP_WAKE_WINDOW_STATUS),
                KEY_SLEEP_DURATION: sleep_data.get(ESP_PROP_SLEEP_DURATION),
                KEY_WAKE_COUNT: sleep_data.get(ESP_PROP_WAKE_COUNT),
            }
        ),
        KEY_TIMESTAMP: time.time(),
    }


def build_threshold_event_payload(
    node_id: str, param_name: str, value: Any
) -> dict[str, Any]:
    """Build threshold data received event data."""
    result: dict[str, Any] = {
        KEY_NODE_ID: node_id,
        KEY_PARAM_NAME: param_name,
        KEY_TIMESTAMP: time.time(),
    }
    if value is not None:
        result[KEY_VALUE] = value
    return result
