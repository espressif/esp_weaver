# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Sensor specifications and threshold configurations for ESP IoT integration.

This module contains sensor-related constants, mappings, and threshold configs.
All sensor metadata is centralized in SENSOR_DEFINITIONS as the source of truth.

Use accessor functions (get_sensor_unit, get_sensor_display_name, etc.) for all lookups.
"""

from dataclasses import dataclass
from typing import Final

# SENSOR TYPE IDENTIFIERS

SENSOR_TYPE_TEMPERATURE: Final[str] = "temperature"
SENSOR_TYPE_AMBIENT_TEMPERATURE: Final[str] = "ambient_temperature"
SENSOR_TYPE_HUMIDITY: Final[str] = "humidity"
SENSOR_TYPE_AMBIENT_HUMIDITY: Final[str] = "ambient_humidity"
SENSOR_TYPE_ILLUMINANCE: Final[str] = "illuminance"
SENSOR_TYPE_PRESSURE: Final[str] = "pressure"
SENSOR_TYPE_CO2: Final[str] = "co2"
SENSOR_TYPE_VOC: Final[str] = "voc"
SENSOR_TYPE_PM25: Final[str] = "pm25"

# SENSOR PARAMETER KEYWORDS
SENSOR_PARAM_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        SENSOR_TYPE_TEMPERATURE,
        "threshold",
        "sensor",
    }
)

# SENSOR TYPE DEFINITIONS


@dataclass(frozen=True)
class SensorDefinition:
    """Immutable sensor definition with all metadata."""

    unit: str
    device_class: str
    icon: str
    display_name: str
    device_param_prefix: (
        str  # For threshold parameter names (e.g., "temp" for temperature)
    )
    base_type: str | None = (
        None  # Maps variant to base type (e.g., ambient_temperature -> temperature)
    )
    state_class: str = "measurement"  # For HA SensorStateClass mapping
    has_number_device_class: bool = True  # Whether HA NumberDeviceClass exists
    display_precision: int | None = None  # Decimal places for HA display


SENSOR_DEFINITIONS: Final[dict[str, SensorDefinition]] = {
    SENSOR_TYPE_TEMPERATURE: SensorDefinition(
        unit="°C",
        device_class=SENSOR_TYPE_TEMPERATURE,
        icon="mdi:thermometer",
        display_name="Temperature",
        device_param_prefix="temp",
        display_precision=1,
    ),
    SENSOR_TYPE_AMBIENT_TEMPERATURE: SensorDefinition(
        unit="°C",
        device_class=SENSOR_TYPE_TEMPERATURE,
        icon="mdi:thermometer",
        display_name="Ambient Temperature",
        device_param_prefix="temp",
        base_type=SENSOR_TYPE_TEMPERATURE,
        display_precision=1,
    ),
    SENSOR_TYPE_HUMIDITY: SensorDefinition(
        unit="%",
        device_class=SENSOR_TYPE_HUMIDITY,
        icon="mdi:water-percent",
        display_name="Humidity",
        device_param_prefix=SENSOR_TYPE_HUMIDITY,
        display_precision=1,
    ),
    SENSOR_TYPE_AMBIENT_HUMIDITY: SensorDefinition(
        unit="%",
        device_class=SENSOR_TYPE_HUMIDITY,
        icon="mdi:water-percent",
        display_name="Ambient Humidity",
        device_param_prefix=SENSOR_TYPE_HUMIDITY,
        base_type=SENSOR_TYPE_HUMIDITY,
        display_precision=1,
    ),
    SENSOR_TYPE_ILLUMINANCE: SensorDefinition(
        unit="lx",
        device_class=SENSOR_TYPE_ILLUMINANCE,
        icon="mdi:brightness-5",
        display_name="Illuminance",
        device_param_prefix=SENSOR_TYPE_ILLUMINANCE,
        has_number_device_class=False,
        display_precision=0,
    ),
    SENSOR_TYPE_PRESSURE: SensorDefinition(
        unit="hPa",
        device_class="atmospheric_pressure",
        icon="mdi:gauge",
        display_name="Pressure",
        device_param_prefix=SENSOR_TYPE_PRESSURE,
        display_precision=1,
    ),
    SENSOR_TYPE_CO2: SensorDefinition(
        unit="ppm",
        device_class="carbon_dioxide",
        icon="mdi:molecule-co2",
        display_name="CO2",
        device_param_prefix=SENSOR_TYPE_CO2,
        has_number_device_class=False,
        display_precision=0,
    ),
    SENSOR_TYPE_VOC: SensorDefinition(
        unit="μg/m³",
        device_class="volatile_organic_compounds",
        icon="mdi:air-filter",
        display_name="VOC",
        device_param_prefix=SENSOR_TYPE_VOC,
        has_number_device_class=False,
        display_precision=0,
    ),
    SENSOR_TYPE_PM25: SensorDefinition(
        unit="μg/m³",
        device_class="pm25",
        icon="mdi:air-filter",
        display_name="PM2.5",
        device_param_prefix=SENSOR_TYPE_PM25,
        has_number_device_class=False,
        display_precision=0,
    ),
}


# ACCESSOR FUNCTIONS


def get_sensor_definition(sensor_type: str) -> SensorDefinition | None:
    """Get sensor definition for a specific sensor type.

    Args:
        sensor_type: The sensor type identifier (e.g., "temperature", "humidity").

    Returns:
        SensorDefinition dataclass or None if sensor type is not defined.
    """
    return SENSOR_DEFINITIONS.get(sensor_type)


def get_sensor_unit(sensor_type: str) -> str:
    """Get unit for sensor type.

    Args:
        sensor_type: The sensor type identifier.

    Returns:
        Unit string or empty string if not found.
    """
    definition = SENSOR_DEFINITIONS.get(sensor_type)
    return definition.unit if definition else ""


def get_sensor_display_name(sensor_type: str) -> str:
    """Get display name for sensor type.

    Args:
        sensor_type: The sensor type identifier.

    Returns:
        Human-readable display name.
    """
    definition = SENSOR_DEFINITIONS.get(sensor_type)
    if definition:
        return definition.display_name
    return sensor_type.replace("_", " ").title()


def get_base_sensor_type(sensor_type: str) -> str:
    """Get base sensor type (resolves variants like ambient_temperature -> temperature).

    Args:
        sensor_type: The sensor type identifier.

    Returns:
        Base sensor type string.
    """
    # Check if it's a variant with a base type
    definition = SENSOR_DEFINITIONS.get(sensor_type)
    if definition and definition.base_type:
        return definition.base_type

    return sensor_type


def get_device_param_prefix(sensor_type: str) -> str:
    """Get device parameter prefix for threshold names.

    Args:
        sensor_type: The sensor type identifier.

    Returns:
        Device parameter prefix (e.g., "temp" for temperature).
    """
    base_type = get_base_sensor_type(sensor_type)
    definition = SENSOR_DEFINITIONS.get(base_type) or SENSOR_DEFINITIONS.get(
        sensor_type
    )
    return definition.device_param_prefix if definition else sensor_type


def get_sensor_display_precision(sensor_type: str) -> int | None:
    """Get display precision for sensor type (0=integer, 1=one decimal, etc.)."""
    definition = SENSOR_DEFINITIONS.get(sensor_type)
    if definition:
        return definition.display_precision
    base_type = get_base_sensor_type(sensor_type)
    definition = SENSOR_DEFINITIONS.get(base_type)
    return definition.display_precision if definition else None


# THRESHOLD CONFIGURATIONS
THRESHOLD_PATTERNS: Final[tuple[str, ...]] = (
    "_min_threshold",
    "_max_threshold",
)


def _build_threshold_keys() -> frozenset[str]:
    """Build threshold keys from sensor definitions.

    Auto-generates threshold parameter names from SENSOR_DEFINITIONS
    to maintain single source of truth.

    Returns:
        Frozenset of all threshold-related parameter names.
    """
    # Include generic threshold configuration keys:
    # - update_interval: polling interval for sensor updates
    # - threshold_alarm_enabled: global toggle for threshold alarms
    keys: set[str] = {"update_interval", "threshold_alarm_enabled"}
    for sensor_def in SENSOR_DEFINITIONS.values():
        prefix = sensor_def.device_param_prefix
        keys.add(f"{prefix}_min_threshold")
        keys.add(f"{prefix}_max_threshold")
    return frozenset(keys)


THRESHOLD_KEYS: Final[frozenset[str]] = _build_threshold_keys()
THRESHOLD_SENSOR_TYPES: Final[frozenset[str]] = frozenset(SENSOR_DEFINITIONS.keys())


@dataclass(frozen=True)
class ThresholdRangeConfig:
    """Configuration for a threshold range (min or max)."""

    range_min: float
    range_max: float
    step: float
    default: float


@dataclass(frozen=True)
class ThresholdConfig:
    """Complete threshold configuration for a sensor type."""

    min_threshold: ThresholdRangeConfig
    max_threshold: ThresholdRangeConfig
    min_icon: str
    max_icon: str


THRESHOLD_CONFIGS: Final[dict[str, ThresholdConfig]] = {
    SENSOR_TYPE_TEMPERATURE: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(-20, 40, 0.5, 15),  # ESP default: 15.0
        max_threshold=ThresholdRangeConfig(-10, 60, 0.5, 30),  # ESP range: -10 to 60
        min_icon="mdi:thermometer-minus",
        max_icon="mdi:thermometer-plus",
    ),
    SENSOR_TYPE_HUMIDITY: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(0, 60, 1, 30),  # ESP default: 30.0
        max_threshold=ThresholdRangeConfig(40, 100, 1, 70),
        min_icon="mdi:water-minus",
        max_icon="mdi:water-plus",
    ),
    SENSOR_TYPE_PRESSURE: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(900, 1000, 1, 950),  # ESP default: 950.0
        max_threshold=ThresholdRangeConfig(1000, 1100, 1, 1050),  # ESP default: 1050.0
        min_icon="mdi:gauge-low",
        max_icon="mdi:gauge-full",
    ),
    SENSOR_TYPE_ILLUMINANCE: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(0, 500, 10, 50),  # ESP default: 50.0
        max_threshold=ThresholdRangeConfig(100, 10000, 50, 1000),
        min_icon="mdi:brightness-5",
        max_icon="mdi:brightness-7",
    ),
    SENSOR_TYPE_CO2: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(300, 800, 25, 400),
        max_threshold=ThresholdRangeConfig(600, 3000, 50, 1000),
        min_icon="mdi:molecule-co2",
        max_icon="mdi:molecule-co2",
    ),
    SENSOR_TYPE_VOC: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(0, 200, 10, 50),
        max_threshold=ThresholdRangeConfig(100, 1000, 25, 300),
        min_icon="mdi:air-filter",
        max_icon="mdi:alert-circle",
    ),
    SENSOR_TYPE_PM25: ThresholdConfig(
        min_threshold=ThresholdRangeConfig(0, 50, 1, 0),  # ESP default: 0.0
        max_threshold=ThresholdRangeConfig(25, 300, 5, 75),
        min_icon="mdi:blur-linear",
        max_icon="mdi:blur",
    ),
}

DEFAULT_THRESHOLD_ICON: Final[str] = "mdi:tune"


def get_threshold_config(sensor_type: str) -> ThresholdConfig | None:
    """Get threshold configuration for a sensor type.

    Automatically resolves variants (ambient_temperature -> temperature).

    Args:
        sensor_type: The sensor type identifier.

    Returns:
        ThresholdConfig or None if not found.
    """
    base_type = get_base_sensor_type(sensor_type)
    return THRESHOLD_CONFIGS.get(base_type)


def get_threshold_icon(sensor_type: str, threshold_type: str) -> str:
    """Get icon for threshold number entity.

    Args:
        sensor_type: The sensor type identifier.
        threshold_type: Either "min" or "max".

    Returns:
        MDI icon string.
    """
    config = get_threshold_config(sensor_type)
    if not config:
        return DEFAULT_THRESHOLD_ICON

    if threshold_type == "min":
        return config.min_icon
    if threshold_type == "max":
        return config.max_icon
    return DEFAULT_THRESHOLD_ICON


def get_threshold_range(
    sensor_type: str, threshold_type: str
) -> dict[str, float] | None:
    """Get threshold range configuration for number entities.

    Args:
        sensor_type: The sensor type identifier.
        threshold_type: Either "min" or "max".

    Returns:
        Dict with min, max, step, default keys or None.
    """
    config = get_threshold_config(sensor_type)
    if not config:
        return None

    if threshold_type == "min":
        range_config = config.min_threshold
    elif threshold_type == "max":
        range_config = config.max_threshold
    else:
        return None  # Invalid threshold_type

    return {
        "min": range_config.range_min,
        "max": range_config.range_max,
        "step": range_config.step,
        "default": range_config.default,
    }


def is_threshold_pattern(param_name: str) -> bool:
    """Check if parameter name matches threshold pattern.

    Args:
        param_name: Parameter name to check.

    Returns:
        True if it's a threshold parameter.
    """
    param_lower = param_name.lower()
    return any(param_lower.endswith(pattern) for pattern in THRESHOLD_PATTERNS)
