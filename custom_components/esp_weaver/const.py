# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Home Assistant-specific constants for ESP-Weaver.

IoT-layer constants (KEY_*, EVENT_*, SENSOR_TYPE_*, etc.) should be imported
directly from iot.specs submodules.
"""

from typing import Final

from homeassistant.const import Platform

# =============================================================================
# PLATFORMS
# =============================================================================

PLATFORMS: Final[tuple[Platform, ...]] = (
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
)


# =============================================================================
# ENTITY DISCOVERY CACHE KEYS
# =============================================================================

CACHE_SENSORS: Final = "sensors"
CACHE_BINARY_SENSORS: Final = "binary_sensors"
CACHE_LIGHTS: Final = "lights"
CACHE_NUMBERS: Final = "numbers"


# =============================================================================
# TIMEOUTS & RETRY SETTINGS
# =============================================================================

DEVICE_SETUP_TIMEOUT: Final = 15
DEFAULT_UPDATE_INTERVAL_SECONDS: Final = 60
RECONNECT_DELAYS: Final[tuple[int, ...]] = (1, 2, 4, 8, 16, 30)
MAX_CONSECUTIVE_FAILURES: Final = 3
DEFAULT_GESTURE_DISPLAY_DURATION: Final = 2.0


# =============================================================================
# PLATFORM IDENTIFIERS
# =============================================================================

PLATFORM_LIGHT: Final = Platform.LIGHT.value
PLATFORM_SENSOR: Final = Platform.SENSOR.value
PLATFORM_BINARY_SENSOR: Final = Platform.BINARY_SENSOR.value
PLATFORM_NUMBER: Final = Platform.NUMBER.value


# =============================================================================
# ENTITY NAMES
# =============================================================================

ENTITY_NAME_LIGHT: Final = "Light"
ENTITY_NAME_BINARY_SENSOR: Final = "Binary Sensor"
ENTITY_NAME_BATTERY_ENERGY: Final = "Battery Energy"
ENTITY_NAME_IMU_GESTURE: Final = "IMU Gesture"
ENTITY_NAME_INTERACTIVE_INPUT: Final = "Interactive Input"
ENTITY_NAME_LOW_POWER_SLEEP: Final = "Low Power Sleep"


# =============================================================================
# ENTITY ATTRIBUTES
# =============================================================================

ATTR_LAST_UPDATED: Final = "last_updated"
ATTR_BINARY_SENSOR_TYPE: Final = "binary_sensor_type"
ATTR_ORIENTATION_X: Final = "orientation_x"
ATTR_ORIENTATION_Y: Final = "orientation_y"
ATTR_ORIENTATION_Z: Final = "orientation_z"
ATTR_ORIENTATION_CHANGE: Final = "orientation_change"

LIGHT_EFFECT_MODE_PREFIX: Final = "Mode "


# =============================================================================
# ALERT TYPES
# =============================================================================

ALERT_TYPE_HIGH: Final = "high"
ALERT_TYPE_LOW: Final = "low"


# =============================================================================
# SERVICE CONSTANTS
# =============================================================================

SERVICE_PERSISTENT_NOTIFICATION: Final = "persistent_notification"
SERVICE_CREATE: Final = "create"
SERVICE_DISMISS: Final = "dismiss"


# =============================================================================
# CONFIG FLOW
# =============================================================================

ERROR_POP_REQUIRED: Final = "pop_required"
ERROR_INVALID_POP: Final = "invalid_pop"
ERROR_INVALID_DEVICE: Final = "invalid_device_selection"
ERROR_CANNOT_CONNECT: Final = "cannot_connect"
ABORT_NO_DEVICES: Final = "no_devices_found"
ABORT_NO_NEW_DEVICES: Final = "no_new_devices_found"
STEP_DEVICE_SETUP: Final = "device_setup"
STEP_POP_INPUT: Final = "pop_input"
FIELD_SELECTED_DEVICE: Final = "selected_device"
PLACEHOLDER_DEVICE_COUNT: Final = "device_count"
PLACEHOLDER_DEVICE_NAME: Final = "device_name"
PLACEHOLDER_DEVICE_IP: Final = "device_ip"


# =============================================================================
# DIAGNOSTICS
# =============================================================================

KEY_PASSWORD: Final = "password"
KEY_TOKEN: Final = "token"
KEY_SECRET: Final = "secret"
KEY_API_KEY: Final = "api_key"
KEY_SERIAL: Final = "serial"
DIAG_ENTRY: Final = "entry"
DIAG_COORDINATOR: Final = "coordinator"
DIAG_API_STATUS: Final = "api_status"
DIAG_DEVICE: Final = "device"
DIAG_DEVICE_DATA: Final = "device_data"
DIAG_DEVICE_DATA_ERROR: Final = "device_data_error"
DIAG_CONNECTIVITY: Final = "connectivity"
DIAG_TITLE: Final = "title"
DIAG_DOMAIN: Final = "domain"
DIAG_VERSION: Final = "version"
DIAG_MINOR_VERSION: Final = "minor_version"
DIAG_DATA: Final = "data"
DIAG_AVAILABLE: Final = "available"
DIAG_DISCOVERY_COMPLETED: Final = "discovery_completed"
DIAG_IS_AVAILABLE: Final = "is_available"
DIAG_DEVICE_REGISTERED: Final = "device_registered"
DIAG_DEVICE_AVAILABLE: Final = "device_available"
DIAG_COORDINATOR_AVAILABLE: Final = "coordinator_available"
