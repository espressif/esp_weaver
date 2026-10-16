# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver binary sensor entity, driven entirely by device profiles."""

import logging
from typing import Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import Platform

from .entity import ESPWeaverOnOffEntity
from .helpers.discovery import binding_platform_setup

_LOGGER = logging.getLogger(__name__)

# Read-only: the coordinator alone updates these entities.
PARALLEL_UPDATES: Final = 0

# Attribute whose reported value replaces the declared device_class. Firmware
# decides at runtime what a generic binary sensor is wired to.
DEVICE_CLASS_ATTRIBUTE: Final = "device_class"

# The state attribute that reports what the firmware settled on, so the answer
# is visible without reading the entity's device_class.
ATTR_BINARY_SENSOR_TYPE: Final = "binary_sensor_type"


class ESPWeaverBinarySensor(ESPWeaverOnOffEntity, BinarySensorEntity):
    """A binary sensor whose parameter mapping comes from a device profile."""

    _device_class_enum = BinarySensorDeviceClass

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the sensor's wiring plus whatever else the profile records.

        The kind of sensor is only stated once the device has said what it is.
        """
        attrs: dict[str, Any] = {}
        if device_class := self._attr_device_class:
            attrs[ATTR_BINARY_SENSOR_TYPE] = device_class.value
        attrs.update(self._extra)
        return attrs

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the state and the device class from a report."""
        changed = self._adopt_on_off(reported)
        changed |= self._apply_device_class(reported)
        return changed

    def _apply_device_class(self, reported: dict[str, Any]) -> bool:
        """Re-read the device class when the firmware reports a new one."""
        if DEVICE_CLASS_ATTRIBUTE not in self._binding.attributes:
            return False

        value = self._read(DEVICE_CLASS_ATTRIBUTE, reported)
        if not isinstance(value, str) or not value:
            return False

        try:
            device_class = BinarySensorDeviceClass(value)
        except ValueError:
            _LOGGER.warning(
                "Device %s reported an unknown device class %r", self._node_id, value
            )
            return False

        if device_class == self._attr_device_class:
            return False

        self._attr_device_class = device_class
        return True


async_setup_entry = binding_platform_setup(
    Platform.BINARY_SENSOR, ESPWeaverBinarySensor
)
