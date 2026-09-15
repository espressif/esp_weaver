# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver number entity, driven entirely by device profiles."""

import logging
from typing import Any, Final

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import Platform, UnitOfPressure
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.unit_conversion import PressureConverter
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .entity import ESPWeaverBindingEntity
from .helpers.discovery import binding_platform_setup
from .helpers.thresholds import threshold_value
from .identity import DOMAIN
from .profiles import THRESHOLD_END_MIN, EntityBinding

_LOGGER = logging.getLogger(__name__)

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# The attribute a number entity reads and writes. Profiles name it `value`
# because a number has no state beyond its own reading.
ATTR_VALUE: Final = "value"


def _to_inhg(value: float, unit: str) -> float:
    """Convert a pressure in the profile's unit to inches of mercury, to two decimals."""
    return round(PressureConverter.convert(value, unit, UnitOfPressure.INHG), 2)


def _from_inhg(inhg: float, unit: str) -> float:
    """Convert inches of mercury back to the profile's unit.

    Rounded to what the profile's unit is read to: a whole pascal, a tenth of
    a hectopascal or millibar, a hundredth of anything coarser.
    """
    value = PressureConverter.convert(inhg, UnitOfPressure.INHG, unit)
    if unit == UnitOfPressure.PA:
        return round(value)
    if unit in (UnitOfPressure.HPA, UnitOfPressure.MBAR):
        return round(value, 1)
    return round(value, 2)


class ESPWeaverNumber(ESPWeaverBindingEntity, NumberEntity):
    """A number whose parameter, range and unit come from a device profile.

    Device settings and alert thresholds alike. The bounds are the device's
    when the profile leaves them out; a profile stating other bounds than the
    device reports produces no entity (profiles/resolver.py).
    """

    _device_class_enum = NumberDeviceClass

    _attr_mode = NumberMode.BOX

    def _configure(self) -> None:
        """Set up the number."""
        binding = self._binding
        self._attr_native_unit_of_measurement = binding.unit

        # Home Assistant converts temperature for a NumberEntity but not
        # pressure; that one is done here from the profile's unit, when the
        # converter knows it.
        self._pressure_unit = (
            binding.unit
            if self._attr_device_class == NumberDeviceClass.ATMOSPHERIC_PRESSURE
            and self.coordinator.hass.config.units is US_CUSTOMARY_SYSTEM
            and binding.unit in PressureConverter.VALID_UNITS
            and binding.unit != UnitOfPressure.INHG
            else None
        )
        if self._pressure_unit is not None:
            self._attr_native_unit_of_measurement = UnitOfPressure.INHG

        self._apply_range(binding)

    def _apply_range(self, binding: EntityBinding) -> None:
        """Adopt the range the firmware reported, or the profile's default."""
        param = binding.attributes.get(ATTR_VALUE)
        bounds = param.spec.bounds if param else None
        if not bounds:
            return

        if (minimum := bounds.get("min")) is not None:
            self._attr_native_min_value = self._to_display(float(minimum))
        if (maximum := bounds.get("max")) is not None:
            self._attr_native_max_value = self._to_display(float(maximum))
        if (step := bounds.get("step")) is not None:
            step = float(step)
            self._attr_native_step = self._to_display(step)

    def _to_display(self, value: float) -> float:
        """Convert a device-side value into the unit the UI shows."""
        if self._pressure_unit is None:
            return value
        return _to_inhg(value, self._pressure_unit)

    def _to_device(self, value: float) -> float:
        """Convert a value the UI produced back into the device's unit."""
        if self._pressure_unit is None:
            return value
        return _from_inhg(value, self._pressure_unit)

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the value the device reported for this parameter."""
        value = self._read(ATTR_VALUE, reported)
        if value is None:
            return False

        try:
            native = self._to_display(float(value))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Device %s reported a non-numeric %s: %r",
                self._node_id,
                self._binding.name,
                value,
            )
            return False

        if native == self._attr_native_value:
            return False

        self._attr_native_value = native
        return True

    async def async_set_native_value(self, value: float) -> None:
        """Send a new value to the device.

        Raises:
            ServiceValidationError: The value would cross the other half of a
                threshold pair.
            HomeAssistantError: The device refused it; the slider is resynced.
        """
        self._reject_crossed_thresholds(value)

        self._attr_native_value = value
        await self._write_optimistic(self._to_device(value), ATTR_VALUE)

    def _reject_crossed_thresholds(self, value: float) -> None:
        """Refuse a minimum above its maximum, or a maximum below its minimum."""
        role = self._binding.threshold
        if role is None or role.partner_key is None:
            return
        paired = threshold_value(self.hass, self._node_id, role.partner_key)
        if paired is None:
            return

        if role.end == THRESHOLD_END_MIN:
            if value >= paired:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="min_threshold_too_high",
                    translation_placeholders={
                        "value": str(value),
                        "max_value": str(paired),
                    },
                )
        elif value <= paired:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="max_threshold_too_low",
                translation_placeholders={
                    "value": str(value),
                    "min_value": str(paired),
                },
            )


async_setup_entry = binding_platform_setup(Platform.NUMBER, ESPWeaverNumber)
