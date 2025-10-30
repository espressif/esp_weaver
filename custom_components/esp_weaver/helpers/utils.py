# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Home Assistant-specific utility functions for ESP-Weaver integration."""

from functools import lru_cache
import logging

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
)

from ..iot.specs.sensor_specs import SENSOR_DEFINITIONS, get_base_sensor_type

_LOGGER = logging.getLogger(__name__)

_UNIT_MAPPING: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "%": PERCENTAGE,
    "hPa": UnitOfPressure.HPA,
    "lx": LIGHT_LUX,
    "ppm": CONCENTRATION_PARTS_PER_MILLION,
    "μg/m³": CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
}

_SENSOR_DEVICE_CLASS_MAPPING: dict[str, SensorDeviceClass] = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "humidity": SensorDeviceClass.HUMIDITY,
    "atmospheric_pressure": SensorDeviceClass.ATMOSPHERIC_PRESSURE,
    "illuminance": SensorDeviceClass.ILLUMINANCE,
    "carbon_dioxide": SensorDeviceClass.CO2,
    "volatile_organic_compounds": SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
    "pm25": SensorDeviceClass.PM25,
}

_NUMBER_DEVICE_CLASS_MAPPING: dict[str, NumberDeviceClass] = {
    "temperature": NumberDeviceClass.TEMPERATURE,
    "humidity": NumberDeviceClass.HUMIDITY,
    "atmospheric_pressure": NumberDeviceClass.ATMOSPHERIC_PRESSURE,
}

_STATE_CLASS_MAPPING: dict[str, SensorStateClass] = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total": SensorStateClass.TOTAL,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}


@lru_cache(maxsize=1)
def get_sensor_mapping() -> dict[
    str, tuple[SensorDeviceClass | None, SensorStateClass, str]
]:
    """Get sensor type to Home Assistant device class mapping."""
    mapping = {}
    for sensor_type, defn in SENSOR_DEFINITIONS.items():
        device_class = _SENSOR_DEVICE_CLASS_MAPPING.get(defn.device_class)
        state_class = _STATE_CLASS_MAPPING.get(
            defn.state_class, SensorStateClass.MEASUREMENT
        )
        unit = _UNIT_MAPPING.get(defn.unit, defn.unit)
        mapping[sensor_type] = (device_class, state_class, unit)
    return mapping


@lru_cache(maxsize=32)
def get_number_device_class_and_unit(
    sensor_type: str,
) -> tuple[NumberDeviceClass | None, str | None]:
    """Get HA device class and unit for number entity."""
    defn = SENSOR_DEFINITIONS.get(sensor_type)
    if not defn:
        defn = SENSOR_DEFINITIONS.get(get_base_sensor_type(sensor_type))
    if not defn:
        return (None, None)

    unit = _UNIT_MAPPING.get(defn.unit, defn.unit)

    # Only return device class if the sensor type has a corresponding NumberDeviceClass
    if defn.has_number_device_class:
        device_class = _NUMBER_DEVICE_CLASS_MAPPING.get(defn.device_class)
        if device_class is None:
            _LOGGER.warning(
                "Sensor type '%s' has_number_device_class=True but device_class '%s' "
                "not found in _NUMBER_DEVICE_CLASS_MAPPING",
                sensor_type,
                defn.device_class,
            )
        return (device_class, unit)

    return (None, unit)


__all__ = [
    "get_number_device_class_and_unit",
    "get_sensor_mapping",
]
