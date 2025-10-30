# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Battery utility functions."""

from dataclasses import dataclass
import logging
from typing import Any

from ..specs.battery_specs import (
    ALERT_LEVEL_CRITICAL,
    ALERT_LEVEL_LOW,
    BATTERY_LEVEL_HIGH,
    BATTERY_LEVEL_LOW,
    BATTERY_LEVEL_MEDIUM,
    CHARGING_STATUS_CHARGING,
    ICON_BATTERY_10,
    ICON_BATTERY_30,
    ICON_BATTERY_50,
    ICON_BATTERY_ALERT,
    ICON_BATTERY_CHARGING,
    ICON_BATTERY_FULL,
    ICON_BATTERY_LOW,
)
from ..specs.keys import (
    KEY_ALERT_LEVEL,
    KEY_BATTERY_LEVEL,
    KEY_CHARGING_STATUS,
    KEY_TEMPERATURE,
    KEY_VOLTAGE,
)

_LOGGER = logging.getLogger(__name__)

# Notification ID patterns
_NOTIFICATION_ID_CRITICAL = "esp_battery_critical_{node_id}"
_NOTIFICATION_ID_LOW = "esp_battery_low_{node_id}"


@dataclass
class ParsedBatteryData:
    """Parsed battery data from raw device event.

    This class is used for parsing raw event data where fields may be None.
    For Home Assistant entity state, use entity_states.BatteryState instead.

    Attributes:
        battery_level: Battery percentage (0-100), or None if not present
        voltage: Battery voltage in volts, or None if not present
        temperature: Battery temperature, or None if not present
        charging_status: Charging status string, or None if not present
        alert_level: Alert level string, or None if not present
    """

    battery_level: int | None = None
    voltage: float | None = None
    temperature: float | None = None
    charging_status: str | None = None
    alert_level: str | None = None


class BatteryProcessor:
    """Processor for battery-related data.

    Handles battery icon selection and data parsing from ESP devices.

    Class Attributes:
        VALID_VOLTAGE_RANGE: Tuple of (min, max) valid voltage values
        MARKER_THRESHOLD: Threshold for detecting invalid marker values
    """

    VALID_VOLTAGE_RANGE = (2.0, 5.0)
    MARKER_THRESHOLD = 40000  # Values above this are likely 0xFFFFFFFF markers

    def get_icon(
        self,
        battery_level: int,
        charging_status: str,
        alert_level: str,
    ) -> str:
        """Return appropriate battery icon based on state.

        Selects the most appropriate Material Design Icon based on battery level,
        charging status, and alert level. Priority order:
        1. Critical/Low alert icons
        2. Charging icon (if charging)
        3. Level-based icons

        Args:
            battery_level: Battery percentage (0-100)
            charging_status: Charging status ("charging", "discharging", etc.)
            alert_level: Alert level ("critical", "low", "normal")

        Returns:
            Material Design Icon string
        """
        # Clamp battery_level to valid range
        battery_level = max(0, min(100, battery_level))

        if alert_level == ALERT_LEVEL_CRITICAL:
            return ICON_BATTERY_ALERT
        if alert_level == ALERT_LEVEL_LOW:
            return ICON_BATTERY_LOW
        if charging_status == CHARGING_STATUS_CHARGING:
            return ICON_BATTERY_CHARGING
        if battery_level > BATTERY_LEVEL_HIGH:
            return ICON_BATTERY_FULL
        if battery_level > BATTERY_LEVEL_MEDIUM:
            return ICON_BATTERY_50
        if battery_level > BATTERY_LEVEL_LOW:
            return ICON_BATTERY_30
        return ICON_BATTERY_10

    def parse_update(self, battery_data: dict[str, Any]) -> ParsedBatteryData:
        """Parse and validate battery data from event.

        Args:
            battery_data: Battery data dictionary (only contains non-None values)

        Returns:
            ParsedBatteryData with parsed properties
        """
        state = ParsedBatteryData()

        if KEY_BATTERY_LEVEL in battery_data:
            try:
                level = int(battery_data[KEY_BATTERY_LEVEL])
                if 0 <= level <= 100:
                    state.battery_level = level
                else:
                    _LOGGER.warning(
                        "Battery level out of range: %d (expected 0-100)", level
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid battery level value: %s", battery_data[KEY_BATTERY_LEVEL]
                )

        if KEY_VOLTAGE in battery_data:
            state.voltage = self._parse_voltage(battery_data[KEY_VOLTAGE])

        if KEY_TEMPERATURE in battery_data:
            try:
                temp_value = float(battery_data[KEY_TEMPERATURE])
                # Validate temperature range (-50°C to 100°C is reasonable for battery)
                if -50.0 <= temp_value <= 100.0:
                    state.temperature = temp_value
                else:
                    _LOGGER.warning(
                        "Battery temp out of range: %.1f°C (-50 to 100)",
                        temp_value,
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid temperature value: %s", battery_data[KEY_TEMPERATURE]
                )

        if KEY_CHARGING_STATUS in battery_data:
            state.charging_status = str(battery_data[KEY_CHARGING_STATUS])

        if KEY_ALERT_LEVEL in battery_data:
            state.alert_level = str(battery_data[KEY_ALERT_LEVEL])

        return state

    def _parse_voltage(self, raw_value: Any) -> float | None:
        """Parse and validate voltage value.

        Handles millivolt conversion and invalid marker detection.
        Conversion logic:
        1. Negative values: Invalid, return None with warning
        2. Values > 1000: Assume millivolts, convert to volts
        3. Values in (100, 1000]: Ambiguous range, return None with warning
        4. Values in [0, 100]: Assume volts (though unusual for battery)

        Args:
            raw_value: Raw voltage value from device

        Returns:
            Validated voltage in volts, or None if invalid
        """
        try:
            raw_voltage = float(raw_value)
        except (ValueError, TypeError):
            return None

        # Check for invalid marker values (0xFFFFFFFF)
        if raw_voltage > self.MARKER_THRESHOLD:
            return None

        # Step 1: Check for invalid negative values
        if raw_voltage < 0:
            _LOGGER.warning("Invalid battery voltage (negative): %s (raw)", raw_voltage)
            return None

        # Step 2: Handle millivolt values (raw > 1000)
        if raw_voltage > 1000:
            voltage = raw_voltage / 1000.0
        # Step 3: Ambiguous range (100, 1000] - could be mV or invalid volts
        elif raw_voltage > 100:
            _LOGGER.warning(
                "Invalid/ambiguous battery voltage: %s (raw, unit unclear)", raw_voltage
            )
            return None
        # Step 4: Normal volt values [0, 100]
        else:
            voltage = raw_voltage

        # Final validation against safe range
        min_v, max_v = self.VALID_VOLTAGE_RANGE
        if voltage < min_v or voltage > max_v:
            _LOGGER.warning(
                "Battery voltage out of safe range: %.2f V (expected %.1f-%.1fV)",
                voltage,
                min_v,
                max_v,
            )
            return None

        return voltage


# Module-Level API (Convenience Functions)
# These functions provide a simpler interface than using processors directly.

_battery_processor = BatteryProcessor()


def get_battery_icon(battery_level: int, charging_status: str, alert_level: str) -> str:
    """Return appropriate battery icon based on state.

    Args:
        battery_level: Battery percentage (0-100)
        charging_status: Charging status ("charging", "discharging", etc.)
        alert_level: Alert level ("critical", "low", "normal")

    Returns:
        Material Design Icon string
    """
    return _battery_processor.get_icon(battery_level, charging_status, alert_level)


def parse_battery_update(battery_data: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate battery data from event.

    Args:
        battery_data: Battery data dictionary (only contains non-None values)

    Returns:
        Dictionary with parsed battery properties
    """
    state = _battery_processor.parse_update(battery_data)

    # Build result dict - only include fields that were in the input
    field_mapping = [
        (state.battery_level, KEY_BATTERY_LEVEL),
        (state.voltage, KEY_VOLTAGE),
        (state.temperature, KEY_TEMPERATURE),
        (state.charging_status, KEY_CHARGING_STATUS),
        (state.alert_level, KEY_ALERT_LEVEL),
    ]

    return {key: value for value, key in field_mapping if value is not None}


# Battery Notification Helpers


def build_battery_notification_data(
    device_name: str,
    node_id: str,
    alert_level: str,
    battery_level: int,
) -> dict[str, Any] | None:
    """Build battery alert notification data.

    Args:
        device_name: Device display name.
        node_id: Device node ID.
        alert_level: Alert level ("critical", "low", "normal").
        battery_level: Current battery percentage.

    Returns:
        Dictionary with notification_id, title, message, or None for normal state.
    """
    if alert_level == ALERT_LEVEL_CRITICAL:
        return {
            "notification_id": _NOTIFICATION_ID_CRITICAL.format(node_id=node_id),
            "title": f"{device_name} - Critical Battery",
            "message": f"Battery critical: {battery_level}% - Charge immediately!",
        }
    if alert_level == ALERT_LEVEL_LOW:
        return {
            "notification_id": _NOTIFICATION_ID_LOW.format(node_id=node_id),
            "title": f"{device_name} - Low Battery",
            "message": f"Battery low: {battery_level}% - Please charge soon.",
        }
    return None


def get_battery_notification_ids_to_clear(node_id: str) -> list[str]:
    """Get notification IDs that should be cleared when battery returns to normal.

    Args:
        node_id: Device node ID.

    Returns:
        List of notification IDs to dismiss.
    """
    return [
        _NOTIFICATION_ID_CRITICAL.format(node_id=node_id),
        _NOTIFICATION_ID_LOW.format(node_id=node_id),
    ]
