# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Battery-related specifications and constants."""

from types import MappingProxyType
from typing import Final

# Note: Field keys (KEY_BATTERY_LEVEL, KEY_VOLTAGE, etc.) are defined in keys.py

# Charging Status Values (match device charging_status values)
CHARGING_STATUS_CHARGING: Final[str] = "charging"
CHARGING_STATUS_DISCHARGING: Final[str] = "discharging"

# Alert Level Values (match device alert_level values)
ALERT_LEVEL_NORMAL: Final[str] = "normal"
ALERT_LEVEL_LOW: Final[str] = "low"
ALERT_LEVEL_CRITICAL: Final[str] = "critical"

# Battery Icons (Material Design Icons)
ICON_BATTERY_ALERT: Final[str] = "mdi:battery-alert"
ICON_BATTERY_LOW: Final[str] = "mdi:battery-low"
ICON_BATTERY_CHARGING: Final[str] = "mdi:battery-charging"
ICON_BATTERY_FULL: Final[str] = "mdi:battery"
ICON_BATTERY_50: Final[str] = "mdi:battery-50"
ICON_BATTERY_30: Final[str] = "mdi:battery-30"
ICON_BATTERY_10: Final[str] = "mdi:battery-10"

# Battery Level Thresholds
# These define boundaries for categorizing battery levels:
# >= HIGH (75): Use ICON_BATTERY_FULL
# >= MEDIUM (50): Use ICON_BATTERY_50
# >= LOW (25): Use ICON_BATTERY_30
# < LOW (25): Use ICON_BATTERY_10 or ICON_BATTERY_LOW
BATTERY_LEVEL_HIGH: Final[int] = 75
BATTERY_LEVEL_MEDIUM: Final[int] = 50
BATTERY_LEVEL_LOW: Final[int] = 25

# Display Mappings (for UI)
# Battery state mappings - matches device charging_status values
BATTERY_STATES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        CHARGING_STATUS_CHARGING: "Charging",
        CHARGING_STATUS_DISCHARGING: "Discharging",
    }
)

# Battery alert levels mapping - matches device alert_level values
BATTERY_ALERT_LEVELS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        ALERT_LEVEL_NORMAL: "Normal",
        ALERT_LEVEL_LOW: "Low Battery",
        ALERT_LEVEL_CRITICAL: "Critical Battery",
    }
)
