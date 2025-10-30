# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device-related specifications and constants."""

from typing import Final

# NETWORK CONNECTION DEFAULTS
DEFAULT_PORT: Final[int] = 8080
DEFAULT_SECURITY_MODE: Final[int] = 1  # 0=no security, 1=PoP, 2=SRP6a

# ESP-RAINMAKER PROPERTY INDICES
CONFIG_PROPERTY_INDEX: Final[int] = 0
PARAMS_PROPERTY_INDEX: Final[int] = 1

# DEVICE INFORMATION DEFAULTS
DEFAULT_DEVICE_NAME: Final[str] = "ESP Device"
DEFAULT_DEVICE_NAME_PREFIX: Final[str] = "ESP-"
DEFAULT_MANUFACTURER: Final[str] = "Espressif"
DEFAULT_MODEL: Final[str] = "ESP Device"

# TIMEOUT CONSTANTS (seconds)
HTTP_CONNECTION_TIMEOUT: Final[float] = 60.0
QUERY_TIMEOUT: Final[float] = 10.0
PROPERTY_SET_TIMEOUT: Final[float] = 5.0
LISTENER_RECV_TIMEOUT: Final[float] = 60.0
LISTENER_ERROR_SLEEP: Final[float] = 1.0
SESSION_CLEANUP_DELAY: Final[float] = 0.1

# TCP KEEPALIVE SETTINGS
TCP_KEEPIDLE: Final[int] = 10
TCP_KEEPINTVL: Final[int] = 5
TCP_KEEPCNT: Final[int] = 3

# BUFFER AND MESSAGE SIZES
SOCKET_RECV_BUFFER_SIZE: Final[int] = 4096
HTTP_HEADER_MIN_LENGTH: Final[int] = 20

# ESP DEVICE PROPERTY NAMES
ESP_PROP_POWER: Final[str] = "Power"
ESP_PROP_BRIGHTNESS: Final[str] = "Brightness"
ESP_PROP_HUE: Final[str] = "Hue"
ESP_PROP_SATURATION: Final[str] = "Saturation"
ESP_PROP_INTENSITY: Final[str] = "Intensity"

ESP_PROP_BATTERY_LEVEL: Final[str] = "Battery Level"
ESP_PROP_VOLTAGE: Final[str] = "Voltage"
ESP_PROP_TEMPERATURE: Final[str] = "Temperature"
ESP_PROP_CHARGING_STATUS: Final[str] = "Charging Status"
ESP_PROP_ALERT_LEVEL: Final[str] = "Alert Level"

ESP_PROP_GESTURE_TYPE: Final[str] = "Gesture Type"
ESP_PROP_GESTURE_CONFIDENCE: Final[str] = "Gesture Confidence"
ESP_PROP_X_ORIENTATION: Final[str] = "X Orientation"
ESP_PROP_Y_ORIENTATION: Final[str] = "Y Orientation"
ESP_PROP_Z_ORIENTATION: Final[str] = "Z Orientation"
ESP_PROP_ORIENTATION_CHANGE: Final[str] = "Orientation Change"
ESP_PROP_SENSITIVITY: Final[str] = "Sensitivity"

ESP_PROP_SHAKE_EVENT: Final[str] = "Shake Event"
ESP_PROP_PUSH_EVENT: Final[str] = "Push Event"
ESP_PROP_CIRCLE_EVENT: Final[str] = "Circle Event"
ESP_PROP_FLIP_EVENT: Final[str] = "Flip Event"
ESP_PROP_TOSS_EVENT: Final[str] = "Toss Event"
ESP_PROP_ROTATION_EVENT: Final[str] = "Rotation Event"
ESP_PROP_CLAP_SINGLE_EVENT: Final[str] = "Clap Single Event"
ESP_PROP_CLAP_DOUBLE_EVENT: Final[str] = "Clap Double Event"
ESP_PROP_CLAP_TRIPLE_EVENT: Final[str] = "Clap Triple Event"

ESP_PROP_INPUT_TYPE: Final[str] = "Input Type"
ESP_PROP_LAST_EVENT: Final[str] = "Last Event"
ESP_PROP_INPUT_EVENTS: Final[str] = "Input Events"
ESP_PROP_INPUT_VALUE: Final[str] = "Input Value"
ESP_PROP_INPUT_CONFIG: Final[str] = "Input Config"
ESP_PROP_INPUT_MAPPING: Final[str] = "Input Mapping"

ESP_PROP_SLEEP_STATE: Final[str] = "Sleep State"
ESP_PROP_WAKE_REASON: Final[str] = "Wake Reason"
ESP_PROP_WAKE_WINDOW_STATUS: Final[str] = "Wake Window Status"
ESP_PROP_SLEEP_DURATION: Final[str] = "Sleep Duration"
ESP_PROP_WAKE_COUNT: Final[str] = "Wake Count"

CONFIG_PROPERTY_NAMES: Final[tuple[str, ...]] = (
    "config",
    "device_config",
    "configuration",
    "setup",
    "info",
)

# DEVICE TYPE NAMES
DEVICE_TYPE_LIGHT: Final[str] = "Light"
DEVICE_TYPE_BINARY_SENSOR: Final[str] = "Binary Sensor"
DEVICE_TYPE_TEMPERATURE_SENSOR: Final[str] = "Temperature Sensor"
DEVICE_TYPE_BATTERY_ENERGY: Final[str] = "Battery & Energy"
DEVICE_TYPE_IMU_GESTURE: Final[str] = "IMU Gesture Sensor"
DEVICE_TYPE_INTERACTIVE_INPUT: Final[str] = "Interactive Input"
DEVICE_TYPE_LOW_POWER_SLEEP: Final[str] = "Low Power & Sleep"

DEVICE_KEY_STATE: Final[str] = "State"

DEVICE_TYPE_MAPPING: Final[dict[str, str | None]] = {
    "esp.device.lightbulb": "light",
    "esp.device.sensor": "sensor",
    "esp.device.temperature-sensor": "sensor",
    "esp.device.humidity-sensor": "sensor",
    "esp.device.binary-sensor": "binary_sensor",
    "esp.device.imu-gesture": "imu_gesture",
    "esp.device.interactive-input": "interactive_input",
    "esp.device.battery-energy": "battery_energy",
    "esp.device.low-power-sleep": "low_power_sleep",
    "esp.service.time": "sensor",
    "esp.service.schedule": None,
    "esp.service.scenes": None,
    "esp.service.system": None,
}
