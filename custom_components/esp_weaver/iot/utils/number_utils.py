# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Number utility functions.

This module provides utilities for number entities including:
- Range configuration for thresholds
- Device parameter name generation
- Threshold parameter parsing
- Unit conversion for imperial users
"""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from ..specs.sensor_specs import (
    get_base_sensor_type,
    get_device_param_prefix,
    get_sensor_unit,
    get_threshold_range,
)

_LOGGER = logging.getLogger(__name__)

# Conversion constants
# Temperature: °C <-> °F
CELSIUS_TO_FAHRENHEIT_FACTOR: float = 9 / 5
FAHRENHEIT_OFFSET: float = 32

# Pressure: hPa <-> inHg (Home Assistant uses inHg for US customary)
HPA_TO_INHG_FACTOR: float = 0.02953
INHG_TO_HPA_FACTOR: float = 33.8639


# NUMBER RANGE CONFIGURATION


def get_number_range_config(sensor_type: str, threshold_type: str) -> dict[str, float]:
    """Get number entity range configuration.

    Args:
        sensor_type: The sensor type identifier
        threshold_type: Either "min" or "max"

    Returns:
        Dictionary with keys: min, max, step, default
    """
    # Default range suitable for generic 0-100 percentage-based thresholds
    default_config: dict[str, float] = {
        "min": 0.0,
        "max": 100.0,
        "step": 1.0,
        "default": 50.0,
    }
    if not sensor_type:
        _LOGGER.warning("Empty sensor_type provided, using default config")
        return default_config
    if threshold_type not in ("min", "max"):
        _LOGGER.warning(
            "Invalid threshold_type '%s' for sensor '%s'",
            threshold_type,
            sensor_type,
        )
        return default_config
    config = get_threshold_range(sensor_type, threshold_type)
    return config if config else default_config


def get_device_threshold_param_name(sensor_type: str, threshold_type: str) -> str:
    """Get device parameter name for threshold.

    Args:
        sensor_type: The sensor type identifier
        threshold_type: Either "min" or "max"

    Returns:
        Device parameter name (e.g., "temp_min_threshold")
    """
    if not sensor_type:
        _LOGGER.error("Empty sensor_type provided to get_device_threshold_param_name")
        raise ValueError("sensor_type must be provided")
    if threshold_type not in ("min", "max"):
        _LOGGER.warning(
            "Invalid threshold_type '%s' for sensor '%s', defaulting to 'min'",
            threshold_type,
            sensor_type,
        )
        threshold_type = "min"
    sensor_prefix = get_device_param_prefix(sensor_type)
    return f"{sensor_prefix}_{threshold_type}_threshold"


# THRESHOLD PARAMETER PARSING


def parse_threshold_params(threshold_data: dict[str, Any] | None) -> set[str]:
    """Parse threshold data to extract sensor types.

    Args:
        threshold_data: Dictionary with threshold parameter names as keys, or None.

    Returns:
        Set of sensor type strings (empty set if threshold_data is None or not a dict).
    """
    if not isinstance(threshold_data, dict):
        return set()
    sensor_types = set()
    for param_name in threshold_data:
        if param_name.endswith("_min_threshold"):
            base = param_name.removesuffix("_min_threshold")
            if base:  # Skip empty strings
                sensor_types.add(base)
        elif param_name.endswith("_max_threshold"):
            base = param_name.removesuffix("_max_threshold")
            if base:  # Skip empty strings
                sensor_types.add(base)
    return sensor_types


def get_sensor_entity_info(sensor_entity: Any) -> dict[str, str] | None:
    """Extract info from a sensor entity for replay.

    Args:
        sensor_entity: Sensor entity object (ESPWeaverSensor instance).

    Returns:
        Dict with keys 'sensor_type', 'node_id', 'sensor_name', or None.
    """
    raw_sensor_type = getattr(sensor_entity, "_sensor_type", None)
    sensor_type = raw_sensor_type.lower() if isinstance(raw_sensor_type, str) else ""
    entity_node_id = getattr(sensor_entity, "_node_id", "")
    sensor_name = getattr(sensor_entity, "name", "")

    if not entity_node_id or not sensor_type:
        return None

    return {
        "sensor_type": sensor_type,
        "node_id": entity_node_id,
        "sensor_name": sensor_name,
    }


# TEMPERATURE UNIT CONVERSION


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit.

    Args:
        celsius: Temperature in Celsius.

    Returns:
        Temperature in Fahrenheit, rounded to 1 decimal.
    """
    return round(celsius * CELSIUS_TO_FAHRENHEIT_FACTOR + FAHRENHEIT_OFFSET, 1)


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """Convert Fahrenheit to Celsius.

    Args:
        fahrenheit: Temperature in Fahrenheit.

    Returns:
        Temperature in Celsius, rounded to 1 decimal.
    """
    return round((fahrenheit - FAHRENHEIT_OFFSET) / CELSIUS_TO_FAHRENHEIT_FACTOR, 1)


# PRESSURE UNIT CONVERSION


def hpa_to_inhg(hpa: float) -> float:
    """Convert hectopascals to inches of mercury.

    Args:
        hpa: Pressure in hectopascals (hPa).

    Returns:
        Pressure in inches of mercury (inHg), rounded to 2 decimals.
    """
    return round(hpa * HPA_TO_INHG_FACTOR, 2)


def inhg_to_hpa(inhg: float) -> float:
    """Convert inches of mercury to hectopascals.

    Args:
        inhg: Pressure in inches of mercury (inHg).

    Returns:
        Pressure in hectopascals (hPa), rounded to 1 decimal.
    """
    return round(inhg * INHG_TO_HPA_FACTOR, 1)


def is_imperial_unit_system(hass: HomeAssistant) -> bool:
    """Check if Home Assistant is configured for imperial/US customary units.

    Args:
        hass: Home Assistant instance.

    Returns:
        True if using imperial/US customary system, False otherwise.
    """
    try:
        return hass.config.units is US_CUSTOMARY_SYSTEM
    except (AttributeError, TypeError):
        return False


# LOCALIZED UNIT FORMATTING FOR LOGGING


def format_value_for_log(
    hass: HomeAssistant,
    value: float,
    sensor_type: str,
    precision: int = 1,
) -> str:
    """Format a sensor value using user's preferred units for logging.

    For US users: shows °F, inHg
    For metric users: shows °C, hPa
    Illuminance always uses lx (HA doesn't auto-convert to fc)

    Args:
        hass: Home Assistant instance.
        value: Value in native units (°C, hPa, lx).
        sensor_type: Sensor type identifier.
        precision: Decimal places for display.

    Returns:
        Formatted string like "77.0 °F" (US) or "25.0 °C" (metric).
    """
    base_type = get_base_sensor_type(sensor_type)
    use_imperial = is_imperial_unit_system(hass)

    if base_type == "temperature":
        if use_imperial:
            return f"{celsius_to_fahrenheit(value):.{precision}f} °F"
        return f"{value:.{precision}f} °C"

    if base_type == "pressure":
        if use_imperial:
            return f"{hpa_to_inhg(value):.2f} inHg"
        return f"{value:.{precision}f} hPa"

    if base_type == "illuminance":
        # HA does not auto-convert lx to fc, so always use lx for consistency
        return f"{int(value)} lx"

    # Humidity and other types - no conversion needed
    unit = get_sensor_unit(sensor_type) or ""
    return f"{value:.{precision}f} {unit}".strip()


def format_range_for_log(
    hass: HomeAssistant,
    min_val: float,
    max_val: float,
    step: float,
    default: float,
    sensor_type: str,
) -> str:
    """Format a threshold range using user's preferred units for logging.

    For US users: shows ranges in °F, inHg
    For metric users: shows ranges in °C, hPa
    Illuminance always uses lx (HA doesn't auto-convert to fc)

    Args:
        hass: Home Assistant instance.
        min_val: Minimum value in native units.
        max_val: Maximum value in native units.
        step: Step value in native units.
        default: Default value in native units.
        sensor_type: Sensor type identifier.

    Returns:
        Formatted string like "range [68.0, 104.0] °F, step=0.9, default=68.0"
    """
    base_type = get_base_sensor_type(sensor_type)
    use_imperial = is_imperial_unit_system(hass)

    if base_type == "temperature":
        if use_imperial:
            imp_min = celsius_to_fahrenheit(min_val)
            imp_max = celsius_to_fahrenheit(max_val)
            imp_step = round(step * CELSIUS_TO_FAHRENHEIT_FACTOR, 1)
            imp_default = celsius_to_fahrenheit(default)
            return (
                f"range [{imp_min:.1f}, {imp_max:.1f}] °F, "
                f"step={imp_step:.1f}, default={imp_default:.1f}"
            )
        return (
            f"range [{min_val:.1f}, {max_val:.1f}] °C, "
            f"step={step:.1f}, default={default:.1f}"
        )

    if base_type == "pressure":
        if use_imperial:
            imp_min = hpa_to_inhg(min_val)
            imp_max = hpa_to_inhg(max_val)
            imp_step = round(step * HPA_TO_INHG_FACTOR, 2)
            imp_default = hpa_to_inhg(default)
            return (
                f"range [{imp_min:.2f}, {imp_max:.2f}] inHg, "
                f"step={imp_step:.2f}, default={imp_default:.2f}"
            )
        return (
            f"range [{min_val:.1f}, {max_val:.1f}] hPa, "
            f"step={step:.1f}, default={default:.1f}"
        )

    if base_type == "illuminance":
        # HA does not auto-convert lx to fc, so always use lx for consistency
        return (
            f"range [{int(min_val)}, {int(max_val)}] lx, "
            f"step={int(step)}, default={int(default)}"
        )

    # Humidity and other types - no conversion needed
    unit = get_sensor_unit(sensor_type) or ""
    return (
        f"range [{min_val:.1f}, {max_val:.1f}] {unit}, "
        f"step={step:.1f}, default={default:.1f}"
    ).strip()


def format_threshold_for_log(
    hass: HomeAssistant,
    threshold_type: str,
    value: float,
    sensor_type: str,
) -> str:
    """Format a threshold value using user's preferred units for logging.

    Args:
        hass: Home Assistant instance.
        threshold_type: "min" or "max".
        value: Threshold value in native units.
        sensor_type: Sensor type identifier.

    Returns:
        Formatted string like "min threshold: 68.0 °F" (US) or "min threshold: 20.0 °C"
    """
    value_str = format_value_for_log(hass, value, sensor_type)
    return f"{threshold_type} threshold: {value_str}"
