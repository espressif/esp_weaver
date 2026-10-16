# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver cover entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
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


class ESPWeaverCover(ESPWeaverPositionEntity, CoverEntity):
    """A cover driven by a single position parameter.

    Opening and closing write the two ends of the travel; there is no motion
    to report in between.
    """

    _device_class_enum = CoverDeviceClass

    def _configure(self) -> None:
        """Set up the cover."""

        features = CoverEntityFeature(0)
        if self._accepts_position():
            features = (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.SET_POSITION
            )
        self._attr_supported_features = features

    @property
    def current_cover_position(self) -> int | None:
        """Return how far open the cover is, from 0 closed to 100 open."""
        return self._position

    @property
    def is_closed(self) -> bool | None:
        """Return True if fully closed, or None until the device has said."""
        position = self.current_cover_position
        return None if position is None else position == POSITION_CLOSED

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover fully."""
        await self._apply({ATTR_POSITION: POSITION_OPEN})

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover fully."""
        await self._apply({ATTR_POSITION: POSITION_CLOSED})

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a given position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return
        await self._apply({ATTR_POSITION: position})


async_setup_entry = binding_platform_setup(Platform.COVER, ESPWeaverCover)
