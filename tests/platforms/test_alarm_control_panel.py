# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven alarm control panel platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.esp_weaver.alarm_control_panel import ESPWeaverAlarmControlPanel
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Security Panel"
ARM_STATE = "Arm State"
FIRE_ALARM = "Fire Alarm State"

STATES = ("disarmed", "armed_home", "armed_away", "armed_night", "triggered")


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _binding(states: tuple[str, ...] = STATES, **overrides: Any) -> EntityBinding:
    """Build a panel binding whose arm state the device also accepts."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.alarm-panel",
        "platform": "alarm_control_panel",
        "name": "Security Panel",
        "key": "alarm_panel",
        "attributes": {
            "state": _param(
                "state",
                AttributeSpec(
                    name="state",
                    param=ARM_STATE,
                    data_type="string",
                    properties=("read", "write"),
                    options=states or None,
                ),
            )
        },
        "extra_attributes": {
            "fire_alarm": _param(
                "fire_alarm",
                AttributeSpec(
                    name="fire_alarm",
                    param=FIRE_ALARM,
                    data_type="bool",
                    properties=("read",),
                ),
            )
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    states: tuple[str, ...] = STATES,
) -> ESPWeaverAlarmControlPanel:
    entity = ESPWeaverAlarmControlPanel(
        coordinator, _binding(states), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "alarm_control_panel.test"
    return entity


class TestAlarmPanelCapabilities:
    """Which arming modes appear follows the values the device listed."""

    def test_offers_the_modes_the_device_named(self, coordinator: MagicMock) -> None:
        """A panel with three arming modes offers exactly those."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_NIGHT
            | AlarmControlPanelEntityFeature.TRIGGER
        )

    def test_a_mode_the_device_never_named_is_not_offered(
        self, coordinator: MagicMock
    ) -> None:
        """A panel without a night mode should not show one."""
        entity = _entity(coordinator, states=("disarmed", "armed_away"))

        assert entity.supported_features == AlarmControlPanelEntityFeature.ARM_AWAY

    def test_no_code_is_asked_for(self, coordinator: MagicMock) -> None:
        """RainMaker has no field for a code, so requiring one blocks arming."""
        entity = _entity(coordinator)

        assert entity.code_arm_required is False


class TestAlarmPanelReads:
    """Turning a report into an arm state."""

    def test_adopts_the_reported_state(self, coordinator: MagicMock) -> None:
        """What the panel says it is is what the entity shows."""
        entity = _entity(coordinator, report={ARM_STATE: "armed_away"})

        assert entity.alarm_state == AlarmControlPanelState.ARMED_AWAY

    def test_the_detectors_are_read_only_attributes(
        self, coordinator: MagicMock
    ) -> None:
        """Each alarm input is detail on the panel, not its own entity."""
        entity = _entity(coordinator, report={ARM_STATE: "disarmed", FIRE_ALARM: True})

        assert entity.extra_state_attributes["fire_alarm"] is True

    def test_an_unknown_state_is_dropped(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A state Home Assistant has no name for would be an invalid state."""
        entity = _entity(coordinator, report={ARM_STATE: "armed_perimeter"})

        assert entity.alarm_state is None
        assert "not one of" in caplog.text


class TestAlarmPanelCommands:
    """Every arming service is a write to the arm state parameter."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            ("async_alarm_disarm", "disarmed"),
            ("async_alarm_arm_home", "armed_home"),
            ("async_alarm_arm_away", "armed_away"),
            ("async_alarm_arm_night", "armed_night"),
            ("async_alarm_trigger", "triggered"),
        ],
    )
    async def test_a_service_writes_the_state_it_asks_for(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        call: str,
        expected: str,
    ) -> None:
        """The panel shows the new state at once instead of at the next poll."""
        entity = _entity(coordinator, hass, report={ARM_STATE: "disarmed"})

        await getattr(entity, call)()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {ARM_STATE: expected}}
        )
        assert entity.alarm_state == AlarmControlPanelState(expected)

    async def test_a_code_from_the_service_call_is_ignored(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """There is nowhere to send it, and refusing would only block arming."""
        entity = _entity(coordinator, hass)

        await entity.async_alarm_arm_away("1234")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {ARM_STATE: "armed_away"}}
        )

    async def test_a_mode_the_device_rejects_is_not_sent(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
    ) -> None:
        """Writing a state the panel never listed would only be refused.

        Said out loud rather than dropped: on a security panel above all, a
        request that quietly does nothing must not look like one that worked.
        """
        entity = _entity(coordinator, hass, states=("disarmed", "armed_away"))

        with pytest.raises(ServiceValidationError) as err:
            await entity.async_alarm_arm_vacation()

        assert err.value.translation_key == "command_not_accepted"
        assert err.value.translation_placeholders is not None
        assert err.value.translation_placeholders["command"] == "arm_vacation"
        coordinator.api.set_params.assert_not_awaited()
