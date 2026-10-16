# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver humidifier entity, driven entirely by device profiles."""

import logging
from typing import Any, Final

from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.const import Platform

from .entity import ATTR_STATE, ESPWeaverPoweredEntity
from .helpers.discovery import binding_platform_setup
from .profiles import EntityBinding

_LOGGER = logging.getLogger(__name__)

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_TARGET_HUMIDITY: Final = "target_humidity"
ATTR_MODE: Final = "mode"
ATTR_CURRENT_HUMIDITY: Final = "current_humidity"


class ESPWeaverHumidifier(ESPWeaverPoweredEntity, HumidifierEntity):
    """A humidifier whose parameter mapping comes from a device profile.

    Only power is required; each further parameter adds its control.
    """

    _device_class_enum = HumidifierDeviceClass

    def _configure(self) -> None:
        """Set up the humidifier."""
        binding = self._binding
        # Home Assistant leaves this one without a default, so a device with no
        # modes has to be told there are none rather than left unset.
        self._attr_available_modes = None
        self._apply_humidity_range(binding)
        self._apply_modes(binding)

    def _apply_humidity_range(self, binding: EntityBinding) -> None:
        """Adopt the setpoint range the firmware reported."""
        target = binding.attributes.get(ATTR_TARGET_HUMIDITY)
        if target is not None and (span := target.span):
            self._attr_min_humidity, self._attr_max_humidity = span

    def _apply_modes(self, binding: EntityBinding) -> None:
        """Offer the mode picker only when the device enumerated its modes."""
        if not self._has(ATTR_MODE):
            return

        modes = self._options(ATTR_MODE)
        if not modes:
            _LOGGER.warning(
                "Device %s reported no values for %s's mode, so it is not offered",
                self._node_id,
                binding.name,
            )
            return

        self._attr_available_modes = modes
        self._attr_supported_features = HumidifierEntityFeature.MODES

    @property
    def target_humidity(self) -> float | None:
        """Return the setpoint, if the device has one."""
        return self._value(ATTR_TARGET_HUMIDITY, float)

    @property
    def current_humidity(self) -> float | None:
        """Return the humidity the device is measuring, if it reports one."""
        return self._value(ATTR_CURRENT_HUMIDITY, float)

    @property
    def mode(self) -> str | None:
        """Return the mode the device is in, if it has modes."""
        return self._value(ATTR_MODE, str)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the humidifier."""
        await self._apply({ATTR_STATE: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the humidifier."""
        await self._apply({ATTR_STATE: False})

    async def async_set_humidity(self, humidity: int) -> None:
        """Move the setpoint."""
        await self._apply({ATTR_TARGET_HUMIDITY: humidity})

    async def async_set_mode(self, mode: str) -> None:
        """Change the mode."""
        await self._apply({ATTR_MODE: mode})


async_setup_entry = binding_platform_setup(Platform.HUMIDIFIER, ESPWeaverHumidifier)
