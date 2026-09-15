# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver climate entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.climate import (
    ATTR_HVAC_MODE as SERVICE_ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError

from .entity import ATTR_STATE, ATTR_TARGET_TEMPERATURE, ESPWeaverThermostatEntity
from .helpers.binding_types import enum_member
from .helpers.discovery import binding_platform_setup
from .identity import DOMAIN

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_HVAC_MODE: Final = "hvac_mode"
ATTR_FAN_MODE: Final = "fan_mode"

# What "on" means for a device that has no mode parameter to ask. Such a device
# heats or cools on its own judgement, which is what auto describes.
FALLBACK_MODE: Final = HVACMode.AUTO


class ESPWeaverClimate(ESPWeaverThermostatEntity, ClimateEntity):
    """A climate device whose parameter mapping comes from a device profile.

    RainMaker keeps power and mode apart; Home Assistant folds them into one
    hvac_mode where off is a choice. Translating between the two is most of
    this class.
    """

    def _configure(self) -> None:
        """Set up the climate entity."""
        self._attr_hvac_modes = self._resolve_hvac_modes()
        self._attr_supported_features = self._resolve_features()
        self._apply_temperature_range()

        self._attr_fan_modes = self._options(ATTR_FAN_MODE) or None

    def _resolve_hvac_modes(self) -> list[HVACMode]:
        """List the modes this device can be put into, off included when there is something to switch off."""
        modes: list[HVACMode] = []
        for value in self._options(ATTR_HVAC_MODE):
            # A value that is no HVAC mode is logged there and skipped here.
            mode = enum_member(self._binding, value, HVACMode)
            if mode is not None and mode not in modes:
                modes.append(mode)
        return self._with_off(modes, FALLBACK_MODE, HVACMode.OFF)

    def _resolve_features(self) -> ClimateEntityFeature:
        """Offer only the controls the device reported a parameter for."""
        features = ClimateEntityFeature(0)
        if self._has(ATTR_TARGET_TEMPERATURE):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._has(ATTR_FAN_MODE):
            features |= ClimateEntityFeature.FAN_MODE
        if HVACMode.OFF in self._attr_hvac_modes:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        return features

    @property
    def _running_mode(self) -> HVACMode | None:
        """Return the mode a device that is on but has not said which is in."""
        return next(
            (mode for mode in self._attr_hvac_modes if mode != HVACMode.OFF), None
        )

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt a report, noting an HVAC mode the device was not known to have."""
        return self._apply_checked(
            reported, ATTR_HVAC_MODE, self._attr_hvac_modes, "HVAC mode"
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the mode the device is in, folding power into it."""
        if self._has(ATTR_STATE) and not self._values.get(ATTR_STATE):
            return HVACMode.OFF

        reported = self._values.get(ATTR_HVAC_MODE)
        if isinstance(reported, str) and reported in self._attr_hvac_modes:
            return HVACMode(reported)

        return self._running_mode

    @property
    def fan_mode(self) -> str | None:
        """Return the fan setting, if the device has one."""
        return self._value(ATTR_FAN_MODE, str)

    def _start_in(self, hvac_mode: HVACMode) -> dict[str, Any]:
        """Return the write for a mode: whichever of power and mode the device has."""
        values: dict[str, Any] = {}
        if self._has(ATTR_STATE):
            values[ATTR_STATE] = True
        if self._has(ATTR_HVAC_MODE):
            values[ATTR_HVAC_MODE] = hvac_mode.value
        return values

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the device to a mode, or off."""
        if hvac_mode == HVACMode.OFF:
            await self._turn_off()
            return

        values = self._start_in(hvac_mode)
        if not values:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_mode_to_set",
                translation_placeholders={"entity": self._binding.name},
            )
        await self._apply(values)

    async def _turn_off(self) -> None:
        """Stop the device, using whichever parameter can do it."""
        if self._has(ATTR_STATE):
            await self._apply({ATTR_STATE: False})
            return
        if self._has(ATTR_HVAC_MODE):
            await self._apply({ATTR_HVAC_MODE: HVACMode.OFF.value})
            return
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_turn_off",
            translation_placeholders={"entity": self._binding.name},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the device in the first mode that is not off."""
        await self.async_set_hvac_mode(self._running_mode or FALLBACK_MODE)

    async def async_turn_off(self) -> None:
        """Stop the device."""
        await self._turn_off()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Move the setpoint, switching mode too when one came with it, in one write."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get(SERVICE_ATTR_HVAC_MODE)

        if hvac_mode == HVACMode.OFF:
            # Off is not a value of the mode parameter on every device, so it
            # goes through the one path that knows which parameter can do it.
            await self._turn_off()
            return

        values: dict[str, Any] = {}
        if temperature is not None:
            values[ATTR_TARGET_TEMPERATURE] = temperature
        if hvac_mode is not None:
            values.update(self._start_in(HVACMode(hvac_mode)))

        if not values:
            return
        await self._apply(values)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Change the fan setting."""
        await self._apply({ATTR_FAN_MODE: fan_mode})


async_setup_entry = binding_platform_setup(Platform.CLIMATE, ESPWeaverClimate)
