# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver alarm control panel entity, driven entirely by device profiles."""

from typing import Final

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.const import Platform

from .entity import ATTR_STATE, ESPWeaverActivityEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Each command, the state it asks for, and the feature it unlocks. A panel
# that never listed a state is not offered the command.
COMMANDS: Final[
    dict[str, tuple[AlarmControlPanelState, AlarmControlPanelEntityFeature]]
] = {
    "arm_home": (
        AlarmControlPanelState.ARMED_HOME,
        AlarmControlPanelEntityFeature.ARM_HOME,
    ),
    "arm_away": (
        AlarmControlPanelState.ARMED_AWAY,
        AlarmControlPanelEntityFeature.ARM_AWAY,
    ),
    "arm_night": (
        AlarmControlPanelState.ARMED_NIGHT,
        AlarmControlPanelEntityFeature.ARM_NIGHT,
    ),
    "arm_vacation": (
        AlarmControlPanelState.ARMED_VACATION,
        AlarmControlPanelEntityFeature.ARM_VACATION,
    ),
    "arm_custom_bypass": (
        AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
        AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS,
    ),
    "trigger": (
        AlarmControlPanelState.TRIGGERED,
        AlarmControlPanelEntityFeature.TRIGGER,
    ),
}

# Disarming is not a feature flag in Home Assistant, so it sits outside that
# table and is offered whenever the panel listed a disarmed state at all.
COMMAND_DISARM: Final = "disarm"


class ESPWeaverAlarmControlPanel(ESPWeaverActivityEntity, AlarmControlPanelEntity):
    """A security panel whose parameter mapping comes from a device profile.

    Arming is one string parameter; each arming service writes to it.
    """

    # RainMaker has no field for a user code, so asking for one would only mean
    # Home Assistant refusing to arm.
    _attr_code_arm_required = False

    def _configure(self) -> None:
        """Set up the panel."""
        wanted = {command: state.value for command, (state, _) in COMMANDS.items()}
        wanted[COMMAND_DISARM] = AlarmControlPanelState.DISARMED.value
        self._bind_commands(ATTR_STATE, wanted)
        self._attr_supported_features = self._resolve_features()

    def _resolve_features(self) -> AlarmControlPanelEntityFeature:
        """Offer only the arming modes the panel listed a state for."""
        return self._command_features(COMMANDS, AlarmControlPanelEntityFeature(0))

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return how the panel is armed."""
        return self._reported(AlarmControlPanelState)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the panel."""
        await self._command(COMMAND_DISARM)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the panel in home mode."""
        await self._command("arm_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the panel in away mode."""
        await self._command("arm_away")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm the panel in night mode."""
        await self._command("arm_night")

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        """Arm the panel in vacation mode."""
        await self._command("arm_vacation")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Arm the panel with the bypass the device defines."""
        await self._command("arm_custom_bypass")

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        """Trigger the alarm."""
        await self._command("trigger")


async_setup_entry = binding_platform_setup(
    Platform.ALARM_CONTROL_PANEL, ESPWeaverAlarmControlPanel
)
