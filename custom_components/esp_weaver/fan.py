# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver fan entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.const import Platform

from .entity import ATTR_STATE, ESPWeaverPoweredEntity
from .helpers.discovery import binding_platform_setup
from .profiles import EntityBinding

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_PERCENTAGE: Final = "percentage"
ATTR_DIRECTION: Final = "direction"


class ESPWeaverFan(ESPWeaverPoweredEntity, FanEntity):
    """A fan whose parameter mapping comes from a device profile.

    Speed and direction are optional; without them it is a plain on/off fan.
    """

    def _configure(self) -> None:
        """Set up the fan."""
        binding = self._binding
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._has(ATTR_PERCENTAGE):
            features |= FanEntityFeature.SET_SPEED
        if self._has(ATTR_DIRECTION):
            features |= FanEntityFeature.DIRECTION
        self._attr_supported_features = features
        self._apply_speed_count(binding)

    def _apply_speed_count(self, binding: EntityBinding) -> None:
        """Offer as many steps as the device has speeds, not a 0-100 slider.

        Counted as the non-zero values in the range, since zero is off: a
        device numbering speeds from one or from zero has as many as its
        maximum either way.
        """
        percentage = binding.attributes.get(ATTR_PERCENTAGE)
        span = percentage.span if percentage else None
        if span is None:
            return
        low, high = span
        steps = int(high) - max(int(low), 1) + 1
        if steps > 0:
            self._attr_speed_count = steps

    @property
    def percentage(self) -> int | None:
        """Return the speed as a percentage, or None if the fan has no speed."""
        if not self._has(ATTR_PERCENTAGE):
            return None
        if not self.is_on:
            return 0
        return self._value(ATTR_PERCENTAGE, int)

    @property
    def current_direction(self) -> str | None:
        """Return the rotation direction, if the device has one."""
        return self._value(ATTR_DIRECTION, str)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, at a given speed if one was asked for."""
        values: dict[str, Any] = {ATTR_STATE: True}
        if percentage is not None and self._has(ATTR_PERCENTAGE):
            values[ATTR_PERCENTAGE] = percentage
        await self._apply(values)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self._apply({ATTR_STATE: False})

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed, treating zero as off the way Home Assistant does."""
        if percentage == 0:
            await self._apply({ATTR_STATE: False})
            return
        await self._apply({ATTR_STATE: True, ATTR_PERCENTAGE: percentage})

    async def async_set_direction(self, direction: str) -> None:
        """Set the rotation direction."""
        await self._apply({ATTR_DIRECTION: direction})


async_setup_entry = binding_platform_setup(Platform.FAN, ESPWeaverFan)
