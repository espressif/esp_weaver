# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Binary sensor specifications and constants."""

from typing import Final

# Note: Field keys (KEY_SENSOR_VALUE, KEY_DEVICE_CLASS, etc.) are defined in keys.py

# Default binary sensor device class
# "door" is chosen as the default because door/window sensors are the most common
# ESP binary sensor use case and map well to Home Assistant's binary_sensor defaults
DEFAULT_BINARY_SENSOR_DEVICE_CLASS: Final[str] = "door"

# Default values for binary sensor properties (in milliseconds)
DEFAULT_DEBOUNCE_TIME_MS: Final[int] = 100
DEFAULT_REPORT_INTERVAL_MS: Final[int] = 1000

# Mapping of string identifiers to BinarySensorDeviceClass values
# Note: This dictionary maps lowercase strings to class name strings
# The actual BinarySensorDeviceClass enum mapping happens in utility functions
BINARY_SENSOR_DEVICE_CLASS_MAP: Final[dict[str, str]] = {
    # Binary sensor device classes supported by ESP devices
    "door": "door",
    "plug": "plug",
    "motion": "motion",
    "vibration": "vibration",
    "touch": "occupancy",  # touch maps to occupancy in HA
}
