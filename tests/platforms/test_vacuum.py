# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven vacuum platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.vacuum import ESPWeaverVacuum
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Vacuum"
STATE = "State"
FAN_SPEED = "Fan Speed"
BATTERY = "Battery"

ACTIVITIES = ("cleaning", "docked", "idle", "paused", "returning", "error")
SPEEDS = ("quiet", "standard", "turbo")


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(
    activities: tuple[str, ...] = ACTIVITIES,
    fan_speed: bool = True,
    writable: bool = True,
) -> dict[str, ParamBinding]:
    """Build the attribute set of a vacuum with the given capabilities."""
    attributes = {
        "activity": _param(
            "activity",
            AttributeSpec(
                name="activity",
                param=STATE,
                data_type="string",
                properties=("read", "write") if writable else ("read",),
                options=activities or None,
            ),
        )
    }
    if fan_speed:
        attributes["fan_speed"] = _param(
            "fan_speed",
            AttributeSpec(
                name="fan_speed",
                param=FAN_SPEED,
                data_type="string",
                properties=("read", "write"),
                options=SPEEDS,
            ),
        )
    return attributes


def _binding(**overrides: Any) -> EntityBinding:
    """Build a vacuum binding with every capability the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.vacuum",
        "platform": "vacuum",
        "name": "Vacuum",
        "key": "vacuum",
        "attributes": _attributes(),
        "extra_attributes": {
            "battery_level": _param(
                "battery_level",
                AttributeSpec(
                    name="battery_level",
                    param=BATTERY,
                    data_type="int",
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
    **overrides: Any,
) -> ESPWeaverVacuum:
    entity = ESPWeaverVacuum(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "vacuum.test"
    return entity


class TestVacuumCapabilities:
    """Which buttons appear follows the values the device listed."""

    def test_a_fully_equipped_vacuum_offers_everything(
        self, coordinator: MagicMock
    ) -> None:
        """Every activity the device named becomes the button that asks for it."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            VacuumEntityFeature.STATE
            | VacuumEntityFeature.START
            | VacuumEntityFeature.PAUSE
            | VacuumEntityFeature.STOP
            | VacuumEntityFeature.RETURN_HOME
            | VacuumEntityFeature.FAN_SPEED
        )
        assert entity.fan_speed_list == list(SPEEDS)

    def test_an_activity_the_device_never_named_gets_no_button(
        self, coordinator: MagicMock
    ) -> None:
        """A vacuum that cannot pause should not be shown a pause button."""
        entity = _entity(
            coordinator,
            attributes=_attributes(activities=("cleaning", "docked", "idle")),
        )

        assert entity.supported_features & VacuumEntityFeature.START
        assert not entity.supported_features & VacuumEntityFeature.PAUSE
        assert not entity.supported_features & VacuumEntityFeature.RETURN_HOME

    def test_a_read_only_activity_leaves_only_the_state(
        self, coordinator: MagicMock
    ) -> None:
        """Firmware that reports without accepting writes has no buttons."""
        entity = _entity(coordinator, attributes=_attributes(writable=False))

        assert entity.supported_features == (
            VacuumEntityFeature.STATE | VacuumEntityFeature.FAN_SPEED
        )

    def test_a_single_speed_vacuum_has_no_speed_picker(
        self, coordinator: MagicMock
    ) -> None:
        """Absent optional parameters must not become dead controls."""
        entity = _entity(coordinator, attributes=_attributes(fan_speed=False))

        assert not entity.supported_features & VacuumEntityFeature.FAN_SPEED
        assert entity.fan_speed_list == []


class TestVacuumReads:
    """Turning a report into an activity."""

    def test_adopts_the_reported_activity_and_speed(
        self, coordinator: MagicMock
    ) -> None:
        """What the device says it is doing is what the entity shows."""
        entity = _entity(
            coordinator, report={STATE: "cleaning", FAN_SPEED: "turbo", BATTERY: 82}
        )

        assert entity.activity == VacuumActivity.CLEANING
        assert entity.fan_speed == "turbo"

    def test_the_battery_is_a_read_only_attribute(self, coordinator: MagicMock) -> None:
        """Home Assistant retired the vacuum battery, so it is plain detail."""
        entity = _entity(coordinator, report={STATE: "docked", BATTERY: 82})

        assert entity.extra_state_attributes["battery_level"] == 82

    def test_an_unknown_activity_is_dropped(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A state Home Assistant has no name for would be an invalid state."""
        entity = _entity(coordinator, report={STATE: "mopping"})

        assert entity.activity is None
        assert "not one of" in caplog.text

    def test_a_spelling_the_profile_did_not_declare_is_not_guessed_at(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A device spelling its states its own way needs a map, not tolerance."""
        entity = _entity(
            coordinator,
            report={STATE: "Cleaning"},
            attributes=_attributes(activities=("Cleaning", "Docked", "Paused")),
        )

        assert entity.activity is None
        assert "the profile needs a map" in caplog.text

    def test_a_command_the_device_spells_differently_is_not_offered(
        self, coordinator: MagicMock
    ) -> None:
        """Writing is as exact as reading, so no button writes an unlisted state."""
        entity = _entity(
            coordinator,
            attributes=_attributes(
                activities=("Cleaning", "Docked", "Paused"), fan_speed=False
            ),
        )

        assert entity.supported_features == VacuumEntityFeature.STATE


class TestVacuumCommands:
    """Every button is a write to the activity parameter."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            ("async_start", "cleaning"),
            ("async_pause", "paused"),
            ("async_stop", "idle"),
            ("async_return_to_base", "returning"),
        ],
    )
    async def test_a_command_writes_the_activity_it_asks_for(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        call: str,
        expected: str,
    ) -> None:
        """The answer shows up at once instead of at the next poll."""
        entity = _entity(coordinator, hass, report={STATE: "docked"})

        await getattr(entity, call)()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {STATE: expected}}
        )
        assert entity.activity == VacuumActivity(expected)

    async def test_a_command_the_device_rejects_is_not_sent(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
    ) -> None:
        """Writing a state the device never listed would only be refused.

        Said out loud rather than dropped: a request that quietly does nothing
        looks exactly like a device that is not listening.
        """
        entity = _entity(
            coordinator, hass, attributes=_attributes(activities=("cleaning", "docked"))
        )

        with pytest.raises(ServiceValidationError) as err:
            await entity.async_pause()

        assert err.value.translation_key == "command_not_accepted"
        assert err.value.translation_placeholders is not None
        assert err.value.translation_placeholders["command"] == "pause"
        coordinator.api.set_params.assert_not_awaited()

    async def test_set_fan_speed_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The speed picker goes straight to the parameter."""
        entity = _entity(coordinator, hass)

        await entity.async_set_fan_speed("quiet")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {FAN_SPEED: "quiet"}}
        )
        assert entity.fan_speed == "quiet"
