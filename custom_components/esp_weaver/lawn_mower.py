# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver lawn mower entity, driven entirely by device profiles."""

from typing import Final

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.const import Platform

from .entity import ATTR_ACTIVITY, ESPWeaverActivityEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Each command, the activity it asks for, and the feature it unlocks.
COMMANDS: Final[dict[str, tuple[LawnMowerActivity, LawnMowerEntityFeature]]] = {
    "start_mowing": (LawnMowerActivity.MOWING, LawnMowerEntityFeature.START_MOWING),
    "pause": (LawnMowerActivity.PAUSED, LawnMowerEntityFeature.PAUSE),
    "dock": (LawnMowerActivity.DOCKED, LawnMowerEntityFeature.DOCK),
}


class ESPWeaverLawnMower(ESPWeaverActivityEntity, LawnMowerEntity):
    """A lawn mower whose parameter mapping comes from a device profile.

    Same shape as the vacuum: one parameter carries what the mower is doing, and
    the three buttons are writes to it.
    """

    def _configure(self) -> None:
        """Set up the lawn mower."""
        self._bind_commands(
            ATTR_ACTIVITY,
            {command: state.value for command, (state, _) in COMMANDS.items()},
        )
        self._attr_supported_features = self._resolve_features()

    def _resolve_features(self) -> LawnMowerEntityFeature:
        """Offer only the buttons the mower listed an activity for."""
        return self._command_features(COMMANDS, LawnMowerEntityFeature(0))

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return what the mower is doing."""
        return self._reported(LawnMowerActivity)

    async def async_start_mowing(self) -> None:
        """Start mowing."""
        await self._command("start_mowing")

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self._command("pause")

    async def async_dock(self) -> None:
        """Send the mower back to its dock."""
        await self._command("dock")


async_setup_entry = binding_platform_setup(Platform.LAWN_MOWER, ESPWeaverLawnMower)
