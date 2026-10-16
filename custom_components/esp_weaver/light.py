# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver light entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import Platform

from .entity import ATTR_STATE, ESPWeaverPoweredEntity
from .helpers.discovery import binding_platform_setup
from .profiles import EntityBinding

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_HUE: Final = "hue"
ATTR_SATURATION: Final = "saturation"
ATTR_COLOR_MODE: Final = "color_mode"

# Which mode to report when the device supports several but never says which
# one is active.
_COLOR_MODE_PRIORITY: Final = (
    ColorMode.HS,
    ColorMode.COLOR_TEMP,
    ColorMode.BRIGHTNESS,
    ColorMode.ONOFF,
)


class ESPWeaverLight(ESPWeaverPoweredEntity, LightEntity):
    """A light whose parameter mapping comes from a device profile.

    What the device reported decides the colour modes: power alone is on/off,
    brightness makes it dimmable, hue/saturation or colour temperature make it
    a colour light.
    """

    def _configure(self) -> None:
        """Set up the light."""
        binding = self._binding
        self._attr_supported_color_modes = _supported_color_modes(binding)

        if effects := self._options(ATTR_EFFECT):
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = effects

        self._apply_color_temp_range(binding)

    def _apply_color_temp_range(self, binding: EntityBinding) -> None:
        """Adopt the colour temperature range the firmware reported."""
        temperature = binding.attributes.get(ATTR_COLOR_TEMP_KELVIN)
        bounds = temperature.spec.bounds if temperature else None
        if not bounds:
            return
        if (minimum := bounds.get("min")) is not None:
            self._attr_min_color_temp_kelvin = int(minimum)
        if (maximum := bounds.get("max")) is not None:
            self._attr_max_color_temp_kelvin = int(maximum)

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt a report, noting a colour mode this light does not offer."""
        return self._apply_checked(
            reported,
            ATTR_COLOR_MODE,
            self._attr_supported_color_modes or (),
            "colour mode",
        )

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the colour mode the light is currently in."""
        supported = self._attr_supported_color_modes or set()
        if len(supported) == 1:
            return next(iter(supported))

        reported = self._values.get(ATTR_COLOR_MODE)
        if isinstance(reported, str) and reported in supported:
            return ColorMode(reported)

        return next((mode for mode in _COLOR_MODE_PRIORITY if mode in supported), None)

    @property
    def brightness(self) -> int | None:
        """Return the brightness, or None while the light is off."""
        brightness = self._values.get(ATTR_BRIGHTNESS)
        if brightness is None or not self.is_on:
            return None
        return int(brightness)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the hue and saturation pair, if the device has both."""
        hue = self._values.get(ATTR_HUE)
        saturation = self._values.get(ATTR_SATURATION)
        if hue is None or saturation is None:
            return None
        return (float(hue), float(saturation))

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the colour temperature in kelvin, if the device has one."""
        return self._value(ATTR_COLOR_TEMP_KELVIN, int)

    @property
    def effect(self) -> str | None:
        """Return the active effect, if the profile declares one."""
        return self._value(ATTR_EFFECT, str)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, applying whatever the service call asked for."""
        values: dict[str, Any] = {ATTR_STATE: True}
        attributes = self._binding.attributes
        supported = self._attr_supported_color_modes or set()

        if ATTR_BRIGHTNESS in kwargs and ATTR_BRIGHTNESS in attributes:
            values[ATTR_BRIGHTNESS] = kwargs[ATTR_BRIGHTNESS]

        if ATTR_HS_COLOR in kwargs and ColorMode.HS in supported:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            values[ATTR_HUE] = hue
            values[ATTR_SATURATION] = saturation
            self._select_color_mode(values, ColorMode.HS)

        if ATTR_COLOR_TEMP_KELVIN in kwargs and ColorMode.COLOR_TEMP in supported:
            values[ATTR_COLOR_TEMP_KELVIN] = kwargs[ATTR_COLOR_TEMP_KELVIN]
            self._select_color_mode(values, ColorMode.COLOR_TEMP)

        if ATTR_EFFECT in kwargs and ATTR_EFFECT in attributes:
            values[ATTR_EFFECT] = kwargs[ATTR_EFFECT]

        await self._apply(values)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._apply({ATTR_STATE: False})

    def _select_color_mode(self, values: dict[str, Any], mode: ColorMode) -> None:
        """Tell a light that does both colour and colour temperature which a write is for."""
        if ATTR_COLOR_MODE in self._binding.attributes:
            values[ATTR_COLOR_MODE] = mode.value


def _supported_color_modes(binding: EntityBinding) -> set[ColorMode]:
    """Derive the colour modes from the attributes the device actually has.

    Home Assistant forbids mixing ONOFF or BRIGHTNESS with a colour mode, so
    the richest capability present wins.
    """
    attributes = binding.attributes
    modes: set[ColorMode] = set()

    if ATTR_HUE in attributes and ATTR_SATURATION in attributes:
        modes.add(ColorMode.HS)
    if ATTR_COLOR_TEMP_KELVIN in attributes:
        modes.add(ColorMode.COLOR_TEMP)
    if modes:
        return modes

    if ATTR_BRIGHTNESS in attributes:
        return {ColorMode.BRIGHTNESS}
    return {ColorMode.ONOFF}


async_setup_entry = binding_platform_setup(Platform.LIGHT, ESPWeaverLight)
