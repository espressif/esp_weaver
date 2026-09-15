# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver vacuum entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.const import Platform

from .entity import ATTR_ACTIVITY, ESPWeaverActivityEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attributes this platform understands; the rest (battery, since Home
# Assistant retired it from vacuums) land in extra_attributes.
ATTR_FAN_SPEED: Final = "fan_speed"

# Each command, the activity it asks for, and the feature it unlocks; only
# activities the firmware listed are offered.
COMMANDS: Final[dict[str, tuple[VacuumActivity, VacuumEntityFeature]]] = {
    "start": (VacuumActivity.CLEANING, VacuumEntityFeature.START),
    "pause": (VacuumActivity.PAUSED, VacuumEntityFeature.PAUSE),
    "stop": (VacuumActivity.IDLE, VacuumEntityFeature.STOP),
    "return_home": (VacuumActivity.RETURNING, VacuumEntityFeature.RETURN_HOME),
}


class ESPWeaverVacuum(ESPWeaverActivityEntity, StateVacuumEntity):
    """A robot vacuum whose parameter mapping comes from a device profile.

    One parameter carries the activity; every button writes to it.
    """

    def _configure(self) -> None:
        """Set up the vacuum."""
        self._bind_commands(
            ATTR_ACTIVITY,
            {command: state.value for command, (state, _) in COMMANDS.items()},
        )
        # Home Assistant leaves this one without a default, so a single-speed
        # vacuum has to be told the list is empty rather than left unset.
        self._attr_fan_speed_list = self._options(ATTR_FAN_SPEED)
        self._attr_supported_features = self._resolve_features()

    def _resolve_features(self) -> VacuumEntityFeature:
        """Offer only the commands and controls this device can take."""
        features = self._command_features(COMMANDS, VacuumEntityFeature.STATE)
        if self._attr_fan_speed_list:
            features |= VacuumEntityFeature.FAN_SPEED
        return features

    @property
    def activity(self) -> VacuumActivity | None:
        """Return what the vacuum is doing."""
        return self._reported(VacuumActivity)

    @property
    def fan_speed(self) -> str | None:
        """Return the suction setting, if the device has one."""
        return self._value(ATTR_FAN_SPEED, str)

    async def async_start(self) -> None:
        """Start cleaning."""
        await self._command("start")

    async def async_pause(self) -> None:
        """Pause cleaning."""
        await self._command("pause")

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning."""
        await self._command("stop")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send the vacuum back to its dock."""
        await self._command("return_home")

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Change the suction setting."""
        await self._apply({ATTR_FAN_SPEED: fan_speed})


async_setup_entry = binding_platform_setup(Platform.VACUUM, ESPWeaverVacuum)
