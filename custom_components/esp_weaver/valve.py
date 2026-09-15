# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver valve entity, driven entirely by device profiles."""

from typing import Final

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.const import Platform

from .entity import (
    ATTR_POSITION,
    POSITION_CLOSED,
    POSITION_OPEN,
    ESPWeaverPositionEntity,
)
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverValve(ESPWeaverPositionEntity, ValveEntity):
    """A valve driven by a single position parameter, like a cover."""

    _device_class_enum = ValveDeviceClass

    def _configure(self) -> None:
        """Set up the valve."""

        # Home Assistant offers the slider only if a position is reported.
        self._attr_reports_position = self._position_param() is not None

        features = ValveEntityFeature(0)
        if self._accepts_position():
            features = (
                ValveEntityFeature.OPEN
                | ValveEntityFeature.CLOSE
                | ValveEntityFeature.SET_POSITION
            )
        self._attr_supported_features = features

    @property
    def current_valve_position(self) -> int | None:
        """Return how far open the valve is, from 0 closed to 100 open."""
        return self._position

    async def async_open_valve(self) -> None:
        """Open the valve fully."""
        await self._apply({ATTR_POSITION: POSITION_OPEN})

    async def async_close_valve(self) -> None:
        """Close the valve fully."""
        await self._apply({ATTR_POSITION: POSITION_CLOSED})

    async def async_set_valve_position(self, position: int) -> None:
        """Move the valve to a given position."""
        await self._apply({ATTR_POSITION: position})


async_setup_entry = binding_platform_setup(Platform.VALVE, ESPWeaverValve)
