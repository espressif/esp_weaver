# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Field key constants for ESP-Weaver data dictionaries."""

from typing import Final

# NETWORK & CONNECTION

KEY_IP: Final[str] = "ip"
KEY_PORT: Final[str] = "port"
KEY_NODE_ID: Final[str] = "node_id"
KEY_TIMESTAMP: Final[str] = "timestamp"
KEY_REGISTERED: Final[str] = "registered"

# DEVICE INFO

KEY_DEVICE_NAME: Final[str] = "device_name"
KEY_DEVICE_INFO: Final[str] = "device_info"
KEY_PARSED_CONFIG: Final[str] = "parsed_config"
KEY_CURRENT_VALUES: Final[str] = "current_values"
KEY_LAST_SUCCESS: Final[str] = "last_success"

# DATA PAYLOAD

KEY_PROPERTIES: Final[str] = "properties"
KEY_VALUE: Final[str] = "value"
KEY_TYPE: Final[str] = "type"
KEY_PARAMS: Final[str] = "params"
KEY_PARAM_NAME: Final[str] = "param_name"
KEY_NAME: Final[str] = "name"
KEY_STATE: Final[str] = "state"
KEY_SOURCE: Final[str] = "source"
KEY_RAW_TYPE: Final[str] = "raw_type"
KEY_ENTITY_ID: Final[str] = "entity_id"
KEY_NOTIFICATION_ID: Final[str] = "notification_id"
KEY_API: Final[str] = "api"
KEY_MAC: Final[str] = "mac"
KEY_POP: Final[str] = "pop"
KEY_INITIAL_DATA: Final[str] = "initial_data"
KEY_INITIAL_VALUE: Final[str] = "initial_value"

# ESP PROTOCOL KEYS

KEY_DEVICES: Final[str] = "devices"
KEY_INFO: Final[str] = "info"
KEY_BOUNDS: Final[str] = "bounds"
KEY_DATA_TYPE: Final[str] = "data_type"
KEY_FLAGS: Final[str] = "flags"
KEY_COUNT: Final[str] = "count"
KEY_STATUS: Final[str] = "status"
KEY_ERROR: Final[str] = "error"
KEY_MODEL: Final[str] = "model"
KEY_HW_VERSION: Final[str] = "hw_version"
KEY_FW_VERSION: Final[str] = "fw_version"
KEY_SW_VERSION: Final[str] = "sw_version"
KEY_PROJECT_NAME: Final[str] = "project_name"
KEY_IDENTIFIERS: Final[str] = "identifiers"
KEY_MANUFACTURER: Final[str] = "manufacturer"
KEY_PLATFORM: Final[str] = "platform"
KEY_MIN: Final[str] = "min"
KEY_MAX: Final[str] = "max"
KEY_STEP: Final[str] = "step"
KEY_CONTENT_LENGTH: Final[str] = "Content-Length"
KEY_TITLE: Final[str] = "title"
KEY_CONFIG: Final[str] = "config"

# INTERNAL STRUCTURE KEYS

KEY_PLATFORMS: Final[str] = "platforms"
KEY_ENTITIES: Final[str] = "entities"
KEY_INITIAL_VALUES: Final[str] = "initial_values"
KEY_ENTITY_TYPE: Final[str] = "entity_type"
KEY_ENTITY_NAME: Final[str] = "entity_name"
KEY_PARAM: Final[str] = "param"
KEY_DISPLAY_NAME: Final[str] = "display_name"
KEY_ICON: Final[str] = "icon"
KEY_PARAM_TYPE: Final[str] = "param_type"
KEY_MIN_VALUE: Final[str] = "min_value"
KEY_MAX_VALUE: Final[str] = "max_value"
KEY_ATTRIBUTES: Final[str] = "attributes"
KEY_FRIENDLY_NAME: Final[str] = "friendly_name"
KEY_ENTITY: Final[str] = "entity"

# PLATFORM TYPE IDENTIFIERS

PLATFORM_TYPE_BATTERY_ENERGY: Final[str] = "battery_energy"
PLATFORM_TYPE_IMU_GESTURE: Final[str] = "imu_gesture"
PLATFORM_TYPE_INTERACTIVE_INPUT: Final[str] = "interactive_input"
PLATFORM_TYPE_LOW_POWER_SLEEP: Final[str] = "low_power_sleep"

# SENSOR

KEY_SENSOR_TYPE: Final[str] = "sensor_type"
KEY_SENSOR_NAME: Final[str] = "sensor_name"
KEY_SENSOR_VALUE: Final[str] = "sensor_value"
KEY_SENSOR_DATA: Final[str] = "sensor_data"
KEY_UNIT_OF_MEASUREMENT: Final[str] = "unit_of_measurement"
KEY_DEVICE_CLASS: Final[str] = "device_class"

# BINARY SENSOR

KEY_DEBOUNCE_TIME: Final[str] = "debounce_time"
KEY_REPORT_INTERVAL: Final[str] = "report_interval"

# BATTERY

KEY_BATTERY_DATA: Final[str] = "battery_data"
KEY_BATTERY_LEVEL: Final[str] = "battery_level"
KEY_VOLTAGE: Final[str] = "voltage"
KEY_TEMPERATURE: Final[str] = "temperature"
KEY_CHARGING_STATUS: Final[str] = "charging_status"
KEY_ALERT_LEVEL: Final[str] = "alert_level"

# GESTURE / IMU

KEY_GESTURE_TYPE: Final[str] = "gesture_type"
KEY_GESTURE: Final[str] = "gesture"
KEY_GESTURE_CONFIDENCE: Final[str] = "gesture_confidence"
KEY_CONFIDENCE: Final[str] = "confidence"
KEY_GESTURE_DISPLAY_DURATION: Final[str] = "gesture_display_duration"
KEY_SENSITIVITY: Final[str] = "sensitivity"
KEY_POWER: Final[str] = "power"

KEY_X_ORIENTATION: Final[str] = "x_orientation"
KEY_Y_ORIENTATION: Final[str] = "y_orientation"
KEY_Z_ORIENTATION: Final[str] = "z_orientation"
KEY_ORIENTATION_CHANGE: Final[str] = "orientation_change"

KEY_ORIENTATION_X: Final[str] = "x"
KEY_ORIENTATION_Y: Final[str] = "y"
KEY_ORIENTATION_Z: Final[str] = "z"
KEY_ORIENTATION_CHANGE_SHORT: Final[str] = "change"

KEY_SHAKE_EVENT: Final[str] = "shake_event"
KEY_PUSH_EVENT: Final[str] = "push_event"
KEY_CIRCLE_EVENT: Final[str] = "circle_event"
KEY_FLIP_EVENT: Final[str] = "flip_event"
KEY_TOSS_EVENT: Final[str] = "toss_event"
KEY_ROTATION_EVENT: Final[str] = "rotation_event"
KEY_CLAP_SINGLE_EVENT: Final[str] = "clap_single_event"
KEY_CLAP_DOUBLE_EVENT: Final[str] = "clap_double_event"
KEY_CLAP_TRIPLE_EVENT: Final[str] = "clap_triple_event"

# INPUT

KEY_INPUT_DATA: Final[str] = "input_data"
KEY_INPUT_TYPE: Final[str] = "input_type"
KEY_LAST_EVENT: Final[str] = "last_event"
KEY_INPUT_EVENTS: Final[str] = "input_events"
KEY_INPUT_VALUE: Final[str] = "input_value"
KEY_INPUT_CONFIG: Final[str] = "input_config"
KEY_INPUT_MAPPING: Final[str] = "input_mapping"

# THRESHOLD

KEY_THRESHOLD_DATA: Final[str] = "threshold_data"

# SLEEP

KEY_SLEEP_DATA: Final[str] = "sleep_data"
KEY_SLEEP_STATE: Final[str] = "sleep_state"
KEY_WAKE_REASON: Final[str] = "wake_reason"
KEY_WAKE_WINDOW_STATUS: Final[str] = "wake_window_status"
KEY_SLEEP_DURATION: Final[str] = "sleep_duration"
KEY_WAKE_COUNT: Final[str] = "wake_count"

# LIGHT

KEY_LIGHT_DATA: Final[str] = "light_data"
KEY_IS_ON: Final[str] = "is_on"
KEY_BRIGHTNESS: Final[str] = "brightness"
KEY_HS_COLOR: Final[str] = "hs_color"
KEY_HUE: Final[str] = "hue"
KEY_SATURATION: Final[str] = "saturation"
KEY_INTENSITY: Final[str] = "intensity"
KEY_LIGHT_MODE: Final[str] = "light_mode"
KEY_COLOR_TEMP: Final[str] = "color_temp"
KEY_COLOR_TEMP_PERCENT: Final[str] = "color_temp_percent"
KEY_COLOR_TEMP_KELVIN: Final[str] = "color_temp_kelvin"
KEY_CCT: Final[str] = "cct"
KEY_LIGHT_MODE_INPUT: Final[str] = "light mode"  # Device input format (with space)
KEY_COLOR_MODE: Final[str] = "color_mode"

# THRESHOLD TYPES

KEY_THRESHOLD_TYPE: Final[str] = "threshold_type"
KEY_THRESHOLD_VALUES: Final[str] = "threshold_values"
THRESHOLD_TYPE_MIN: Final[str] = "min"
THRESHOLD_TYPE_MAX: Final[str] = "max"

# SOURCE IDENTIFIERS

SOURCE_DEVICE_THRESHOLD_REPORT: Final[str] = "device_threshold_report"
SOURCE_NUMBER_ENTITY: Final[str] = "number_entity"

# CONFIG ENTRY KEYS

CONF_NODE_ID: Final[str] = "node_id"
CONF_CUSTOM_POP: Final[str] = "custom_pop"
CONF_SECURITY_VERSION: Final[str] = "security_version"
