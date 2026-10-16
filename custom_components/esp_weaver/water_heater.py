# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver water heater entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE as SERVICE_ATTR_OPERATION_MODE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, Platform
from homeassistant.exceptions import HomeAssistantError

from .entity import ATTR_STATE, ATTR_TARGET_TEMPERATURE, ESPWeaverThermostatEntity
from .helpers.discovery import binding_platform_setup
from .identity import DOMAIN

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_OPERATION_MODE: Final = "operation_mode"

# The operation of a device with no mode parameter while it is on.
FALLBACK_OPERATION: Final = "heat"


class ESPWeaverWaterHeater(ESPWeaverThermostatEntity, WaterHeaterEntity):
    """A water heater whose parameter mapping comes from a device profile.

    Power and mode are folded into one operation list with off, as the climate
    platform does for hvac_mode.
    """

    def _configure(self) -> None:
        """Set up the water heater."""
        self._attr_operation_list = self._resolve_operations()
        self._attr_supported_features = self._resolve_features()
        self._apply_temperature_range()

    def _resolve_operations(self) -> list[str]:
        """List the operations this device can be put into, off included when there is power."""
        operations = list(dict.fromkeys(self._options(ATTR_OPERATION_MODE)))
        return self._with_off(operations, FALLBACK_OPERATION, STATE_OFF)

    def _resolve_features(self) -> WaterHeaterEntityFeature:
        """Offer only the controls the device reported a parameter for."""
        features = WaterHeaterEntityFeature(0)
        if self._has(ATTR_TARGET_TEMPERATURE):
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        if self._has(ATTR_OPERATION_MODE) or self._has(ATTR_STATE):
            features |= WaterHeaterEntityFeature.OPERATION_MODE
        if self._has(ATTR_STATE):
            features |= WaterHeaterEntityFeature.ON_OFF
        return features

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt a report, noting an operation the device was not known to have."""
        return self._apply_checked(
            reported, ATTR_OPERATION_MODE, self._attr_operation_list or (), "operation"
        )

    @property
    def current_operation(self) -> str | None:
        """Return what the heater is doing, with power taking precedence."""
        if self._has(ATTR_STATE) and not self._values.get(ATTR_STATE):
            return STATE_OFF

        reported = self._values.get(ATTR_OPERATION_MODE)
        operations = self._attr_operation_list or []
        if isinstance(reported, str) and reported in operations:
            return reported

        return next(
            (operation for operation in operations if operation != STATE_OFF), None
        )

    def _start_in(self, operation: str) -> dict[str, Any]:
        """Return the write for an operation: whichever of power and mode the device has."""
        values: dict[str, Any] = {}
        if self._has(ATTR_STATE):
            values[ATTR_STATE] = True
        if self._has(ATTR_OPERATION_MODE):
            values[ATTR_OPERATION_MODE] = operation
        return values

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Switch the heater to an operation, or off."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return

        values = self._start_in(operation_mode)
        if not values:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_operation_to_set",
                translation_placeholders={"entity": self._binding.name},
            )
        await self._apply(values)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the heater."""
        await self._apply({ATTR_STATE: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the heater."""
        if not self._has(ATTR_STATE):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_turn_off",
                translation_placeholders={"entity": self._binding.name},
            )
        await self._apply({ATTR_STATE: False})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Move the setpoint, switching operation too when one came with it, in one write."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        operation_mode = kwargs.get(SERVICE_ATTR_OPERATION_MODE)

        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return

        values: dict[str, Any] = {}
        if temperature is not None:
            values[ATTR_TARGET_TEMPERATURE] = temperature
        if operation_mode is not None:
            values.update(self._start_in(operation_mode))

        if not values:
            return
        await self._apply(values)


async_setup_entry = binding_platform_setup(Platform.WATER_HEATER, ESPWeaverWaterHeater)
