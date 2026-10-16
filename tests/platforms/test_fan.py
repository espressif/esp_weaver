# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven fan platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.fan import FanEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.fan import ESPWeaverFan
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Fan"
POWER = "Power"
SPEED = "Speed"
DIRECTION = "Direction"

# The device has five speeds, which the profile scales onto a percentage.
SPEED_COUNT = 5


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(speed: bool = True, direction: bool = True) -> dict[str, ParamBinding]:
    """Build the attribute set of a fan with the given capabilities."""
    attributes = {
        "state": _param(
            "state",
            AttributeSpec(
                name="state",
                param=POWER,
                data_type="bool",
                properties=("read", "write"),
            ),
        )
    }
    if speed:
        attributes["percentage"] = _param(
            "percentage",
            AttributeSpec(
                name="percentage",
                param=SPEED,
                data_type="int",
                properties=("read", "write"),
                bounds={"min": 0, "max": SPEED_COUNT, "step": 1},
                scale={"to": [0, 100]},
            ),
        )
    if direction:
        attributes["direction"] = _param(
            "direction",
            AttributeSpec(
                name="direction",
                param=DIRECTION,
                data_type="int",
                properties=("read", "write"),
                bounds={"min": 0, "max": 1, "step": 1},
                value_map={0: "forward", 1: "reverse"},
            ),
        )
    return attributes


def _binding(**overrides: Any) -> EntityBinding:
    """Build a fan binding with every capability the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.fan",
        "platform": "fan",
        "name": "Fan",
        "key": "fan",
        "attributes": _attributes(),
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverFan:
    entity = ESPWeaverFan(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "fan.test"
    return entity


class TestFanCapabilities:
    """What the fan offers depends on what the device reported."""

    def test_power_only_fan_has_neither_speed_nor_direction(
        self, coordinator: MagicMock
    ) -> None:
        """Absent optional parameters must not become dead controls."""
        entity = _entity(
            coordinator, attributes=_attributes(speed=False, direction=False)
        )

        assert entity.supported_features == (
            FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        )
        assert entity.percentage is None
        assert entity.current_direction is None

    def test_speed_and_direction_unlock_their_features(
        self, coordinator: MagicMock
    ) -> None:
        """A fully equipped fan offers everything."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.SET_SPEED
            | FanEntityFeature.DIRECTION
        )

    def test_offers_as_many_steps_as_the_device_has_speeds(
        self, coordinator: MagicMock
    ) -> None:
        """The slider snaps to real speeds instead of arbitrary percentages."""
        entity = _entity(coordinator)

        assert entity.speed_count == SPEED_COUNT

    def test_a_fan_numbering_its_speeds_from_one_keeps_all_of_them(
        self, coordinator: MagicMock
    ) -> None:
        """Zero is off rather than a speed, so it is off the count either way.

        A device whose range starts at one has no off inside it, and counting
        the span instead of the values would lose it a speed.
        """
        attributes = _attributes()
        attributes["percentage"] = _param(
            "percentage",
            AttributeSpec(
                name="percentage",
                param=SPEED,
                data_type="int",
                properties=("read", "write"),
                bounds={"min": 1, "max": SPEED_COUNT, "step": 1},
                scale={"to": [0, 100]},
            ),
        )
        entity = _entity(coordinator, attributes=attributes)

        assert entity.speed_count == SPEED_COUNT


class TestFanReads:
    """Turning a report into speed and direction."""

    def test_scales_the_reported_speed(self, coordinator: MagicMock) -> None:
        """Speed 3 of 5 is 60 percent."""
        entity = _entity(coordinator, report={POWER: True, SPEED: 3})

        assert entity.is_on is True
        assert entity.percentage == 60

    def test_speed_reads_zero_while_off(self, coordinator: MagicMock) -> None:
        """A stopped fan is at zero, whatever speed it would resume at."""
        entity = _entity(coordinator, report={POWER: False, SPEED: 3})

        assert entity.is_on is False
        assert entity.percentage == 0

    def test_names_the_reported_direction(self, coordinator: MagicMock) -> None:
        """The direction number is reported under the name the profile gives."""
        entity = _entity(coordinator, report={POWER: True, DIRECTION: 1})

        assert entity.current_direction == "reverse"


class TestFanWrites:
    """Starting, stopping and adjusting the fan."""

    async def test_turn_on_with_a_speed_writes_one_payload(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Power and speed travel together so the fan never starts at the old one."""
        entity = _entity(coordinator, hass)

        await entity.async_turn_on(percentage=60)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, SPEED: 3}}
        )

    async def test_setting_a_speed_also_starts_the_fan(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A speed asked of a stopped fan is a request to run at it."""
        entity = _entity(coordinator, hass, report={POWER: False})

        await entity.async_set_percentage(100)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, SPEED: SPEED_COUNT}}
        )

    async def test_zero_percent_stops_the_fan(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Home Assistant treats zero as off, and so does the device."""
        entity = _entity(coordinator, hass, report={POWER: True, SPEED: 3})

        await entity.async_set_percentage(0)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.is_on is False

    async def test_set_direction_writes_the_mapped_number(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The direction name becomes the number the device expects."""
        entity = _entity(coordinator, hass)

        await entity.async_set_direction("reverse")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {DIRECTION: 1}}
        )
        assert entity.current_direction == "reverse"

    async def test_turn_off_writes_power_alone(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Stopping the fan must not disturb the speed it will resume at."""
        entity = _entity(coordinator, hass, report={POWER: True, SPEED: 3})

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
