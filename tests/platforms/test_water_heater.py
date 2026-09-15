# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven water heater platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.water_heater import (
    FALLBACK_OPERATION,
    ESPWeaverWaterHeater,
)
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Water Heater"
POWER = "Power"
SETPOINT = "Setpoint Temperature"
MODE = "Mode"
CURRENT = "Temperature"

OPERATIONS = ("eco", "performance")


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(
    power: bool = True, setpoint: bool = True, mode: bool = True
) -> dict[str, ParamBinding]:
    """Build the attribute set of a heater with the given capabilities."""
    attributes: dict[str, ParamBinding] = {
        "current_temperature": _param(
            "current_temperature",
            AttributeSpec(
                name="current_temperature",
                param=CURRENT,
                data_type="float",
                properties=("read",),
            ),
        )
    }
    if power:
        attributes["state"] = _param(
            "state",
            AttributeSpec(
                name="state",
                param=POWER,
                data_type="bool",
                properties=("read", "write"),
            ),
        )
    if setpoint:
        attributes["target_temperature"] = _param(
            "target_temperature",
            AttributeSpec(
                name="target_temperature",
                param=SETPOINT,
                data_type="float",
                properties=("read", "write"),
                bounds={"min": 30, "max": 75, "step": 1},
            ),
        )
    if mode:
        attributes["operation_mode"] = _param(
            "operation_mode",
            AttributeSpec(
                name="operation_mode",
                param=MODE,
                data_type="string",
                properties=("read", "write"),
                options=OPERATIONS,
            ),
        )
    return attributes


def _binding(**overrides: Any) -> EntityBinding:
    """Build a heater binding with every capability the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.water-heater-control",
        "platform": "water_heater",
        "name": "Water Heater",
        "key": "water_heater",
        "attributes": _attributes(),
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverWaterHeater:
    entity = ESPWeaverWaterHeater(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "water_heater.test"
    return entity


class TestWaterHeaterCapabilities:
    """What the heater offers depends on what the device reported."""

    def test_off_joins_the_operations_a_device_named(
        self, coordinator: MagicMock
    ) -> None:
        """Home Assistant switches a heater off by picking off as an operation."""
        entity = _entity(coordinator)

        assert entity.operation_list == [STATE_OFF, *OPERATIONS]
        assert entity.supported_features == (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )

    def test_a_device_without_modes_still_has_something_to_be_in(
        self, coordinator: MagicMock
    ) -> None:
        """A heater with only power is on or off, and on means heating."""
        entity = _entity(coordinator, attributes=_attributes(mode=False))

        assert entity.operation_list == [STATE_OFF, FALLBACK_OPERATION]

    def test_a_device_without_power_cannot_be_switched_off(
        self, coordinator: MagicMock
    ) -> None:
        """Off is only offered when there is a parameter that does it."""
        entity = _entity(coordinator, attributes=_attributes(power=False))

        assert entity.operation_list == list(OPERATIONS)
        assert not entity.supported_features & WaterHeaterEntityFeature.ON_OFF

    def test_setpoint_range_follows_the_device(self, coordinator: MagicMock) -> None:
        """A heater that stops at 75 should not be asked for more."""
        entity = _entity(coordinator)

        assert entity.min_temp == 30
        assert entity.max_temp == 75
        assert entity.target_temperature_step == 1
        assert entity.temperature_unit == UnitOfTemperature.CELSIUS


class TestWaterHeaterReads:
    """Turning a report into an operation and temperatures."""

    def test_power_off_reads_as_off_whatever_the_mode_says(
        self, coordinator: MagicMock
    ) -> None:
        """The mode a stopped heater will resume in is not what it is doing."""
        entity = _entity(coordinator, report={POWER: False, MODE: "eco"})

        assert entity.current_operation == STATE_OFF

    def test_adopts_the_reported_operation_and_temperatures(
        self, coordinator: MagicMock
    ) -> None:
        """One report carries the mode, the setpoint and the water temperature."""
        entity = _entity(
            coordinator,
            report={POWER: True, MODE: "performance", SETPOINT: 60, CURRENT: 48.5},
        )

        assert entity.current_operation == "performance"
        assert entity.target_temperature == 60
        assert entity.current_temperature == 48.5

    def test_an_unknown_operation_falls_back(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A mode Home Assistant was never told about would be an invalid state."""
        entity = _entity(coordinator, report={POWER: True, MODE: "turbo"})

        assert entity.current_operation == OPERATIONS[0]
        assert "unknown operation" in caplog.text


class TestWaterHeaterWrites:
    """Switching the heater and moving the setpoint."""

    async def test_picking_an_operation_also_starts_the_heater(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Power and mode travel together so the heater never runs the old one."""
        entity = _entity(coordinator, hass, report={POWER: False, MODE: "eco"})

        await entity.async_set_operation_mode("performance")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, MODE: "performance"}}
        )
        assert entity.current_operation == "performance"

    async def test_picking_off_writes_power_alone(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Stopping must not disturb the mode it will resume in."""
        entity = _entity(coordinator, hass, report={POWER: True, MODE: "eco"})

        await entity.async_set_operation_mode(STATE_OFF)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.current_operation == STATE_OFF

    async def test_turn_on_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The on switch is the power parameter."""
        entity = _entity(coordinator, hass, report={POWER: False})

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )

    async def test_a_heater_without_power_says_so_instead_of_failing_quietly(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Refusing out loud is better than a switch that does nothing."""
        entity = _entity(coordinator, hass, attributes=_attributes(power=False))

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()

    async def test_set_temperature_writes_the_setpoint(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The slider goes straight to the parameter."""
        entity = _entity(coordinator, hass)

        await entity.async_set_temperature(temperature=55)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {SETPOINT: 55}}
        )

    async def test_a_call_without_a_temperature_does_nothing(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Only the ranged form of the service carries high and low."""
        entity = _entity(coordinator, hass)

        await entity.async_set_temperature()

        coordinator.api.set_params.assert_not_awaited()
