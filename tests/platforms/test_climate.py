# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven climate platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.esp_weaver.climate import ESPWeaverClimate
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Air Conditioner"
POWER = "Power"
MODE = "AC Mode"
CURRENT = "Temperature"
SETPOINT = "Setpoint Temperature"
FAN = "Fan Speed"

# The RainMaker spelling of each mode, and the Home Assistant one it maps to.
MODE_MAP = {
    "Off": "off",
    "Cool": "cool",
    "Heat": "heat",
    "Auto": "auto",
    "Dry": "dry",
    "Fan": "fan_only",
}
FAN_MAP = {
    0: "off",
    1: "low",
    2: "medium_low",
    3: "medium",
    4: "medium_high",
    5: "high",
}


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(
    power: bool = True,
    mode: bool = True,
    fan: bool = True,
    current: bool = True,
) -> dict[str, ParamBinding]:
    """Build the attribute set of a climate device with given capabilities."""
    attributes: dict[str, ParamBinding] = {}
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
    if mode:
        attributes["hvac_mode"] = _param(
            "hvac_mode",
            AttributeSpec(
                name="hvac_mode",
                param=MODE,
                data_type="string",
                properties=("read", "write"),
                value_map=dict(MODE_MAP),
            ),
        )
    if current:
        attributes["current_temperature"] = _param(
            "current_temperature",
            AttributeSpec(
                name="current_temperature",
                param=CURRENT,
                data_type="float",
                properties=("read",),
            ),
        )
    attributes["target_temperature"] = _param(
        "target_temperature",
        AttributeSpec(
            name="target_temperature",
            param=SETPOINT,
            data_type="float",
            properties=("read", "write"),
            bounds={"min": 16, "max": 30, "step": 0.5},
        ),
    )
    if fan:
        attributes["fan_mode"] = _param(
            "fan_mode",
            AttributeSpec(
                name="fan_mode",
                param=FAN,
                data_type="int",
                properties=("read", "write"),
                bounds={"min": 0, "max": 5, "step": 1},
                value_map=dict(FAN_MAP),
            ),
        )
    return attributes


def _binding(**overrides: Any) -> EntityBinding:
    """Build a climate binding with every capability the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.air-conditioner",
        "platform": "climate",
        "name": "Air Conditioner",
        "key": "air_conditioner",
        "attributes": _attributes(),
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverClimate:
    entity = ESPWeaverClimate(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "climate.test"
    return entity


class TestClimateCapabilities:
    """What the device offers depends on the parameters it reported."""

    def test_modes_come_from_the_profile(self, coordinator: MagicMock) -> None:
        """The mode list is what the map says, not a fixed set."""
        entity = _entity(coordinator)

        assert entity.hvac_modes == [
            HVACMode.OFF,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.AUTO,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        ]

    def test_offers_every_control_it_has_a_parameter_for(
        self, coordinator: MagicMock
    ) -> None:
        """A fully equipped device can be aimed, fanned and switched off."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        assert entity.fan_modes == list(FAN_MAP.values())

    def test_adopts_the_reported_setpoint_range(self, coordinator: MagicMock) -> None:
        """The slider covers what the firmware said it accepts."""
        entity = _entity(coordinator)

        assert entity.min_temp == 16
        assert entity.max_temp == 30
        assert entity.target_temperature_step == 0.5

    def test_a_setpoint_only_device_is_never_off(self, coordinator: MagicMock) -> None:
        """With no power parameter there is nothing to switch off."""
        entity = _entity(
            coordinator, attributes=_attributes(power=False, mode=False, fan=False)
        )

        assert entity.hvac_modes == [HVACMode.AUTO]
        assert entity.hvac_mode == HVACMode.AUTO
        assert entity.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE

    def test_an_unknown_mode_in_the_profile_is_dropped(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A profile naming a mode Home Assistant lacks is a profile bug."""
        attributes = _attributes()
        attributes["hvac_mode"] = _param(
            "hvac_mode",
            AttributeSpec(
                name="hvac_mode",
                param=MODE,
                data_type="string",
                properties=("read", "write"),
                value_map={"Cool": "cool", "Turbo": "turbo"},
            ),
        )
        entity = _entity(coordinator, attributes=attributes)

        assert entity.hvac_modes == [HVACMode.OFF, HVACMode.COOL]
        assert "is not a valid HVACMode" in caplog.text


class TestClimateReads:
    """Folding power and mode into one state."""

    def test_power_off_shows_off(self, coordinator: MagicMock) -> None:
        """A device that is off is off, whatever mode it would resume in."""
        entity = _entity(coordinator, report={POWER: False, MODE: "Cool"})

        assert entity.hvac_mode == HVACMode.OFF

    def test_power_on_shows_the_mode(self, coordinator: MagicMock) -> None:
        """A running device shows the mode it is running in."""
        entity = _entity(coordinator, report={POWER: True, MODE: "Cool"})

        assert entity.hvac_mode == HVACMode.COOL

    def test_reads_both_temperatures(self, coordinator: MagicMock) -> None:
        """The measured and the wanted temperature are separate readings."""
        entity = _entity(
            coordinator, report={POWER: True, CURRENT: 24.5, SETPOINT: 21.0}
        )

        assert entity.current_temperature == 24.5
        assert entity.target_temperature == 21.0

    def test_names_the_reported_fan_speed(self, coordinator: MagicMock) -> None:
        """The fan speed number is shown under the name the profile gives."""
        entity = _entity(coordinator, report={POWER: True, FAN: 5})

        assert entity.fan_mode == "high"

    def test_an_unknown_reported_mode_falls_back(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A mode outside the list must not become an invalid state."""
        entity = _entity(coordinator, report={POWER: True, MODE: "Turbo"})

        assert entity.hvac_mode == HVACMode.COOL
        assert "unknown HVAC mode" in caplog.text


class TestClimateWrites:
    """Changing mode, setpoint and fan speed."""

    async def test_setting_a_mode_also_powers_on(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Home Assistant asking for heat means on and heating, in one write."""
        entity = _entity(coordinator, hass, report={POWER: False})

        await entity.async_set_hvac_mode(HVACMode.HEAT)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, MODE: "Heat"}}
        )
        assert entity.hvac_mode == HVACMode.HEAT

    async def test_off_cuts_the_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Off is the power parameter, leaving the mode to resume from."""
        entity = _entity(coordinator, hass, report={POWER: True, MODE: "Cool"})

        await entity.async_set_hvac_mode(HVACMode.OFF)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.hvac_mode == HVACMode.OFF

    async def test_off_without_power_uses_the_mode(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A device with only a mode parameter is stopped through it."""
        entity = _entity(coordinator, hass, attributes=_attributes(power=False))

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {MODE: "Off"}}
        )

    async def test_a_device_that_cannot_stop_says_so(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Refusing beats pretending the device was switched off."""
        entity = _entity(
            coordinator, hass, attributes=_attributes(power=False, mode=False)
        )

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_off()
        assert err.value.translation_key == "cannot_turn_off"

        coordinator.api.set_params.assert_not_called()

    async def test_set_temperature_writes_the_setpoint(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Only the setpoint moves, not the mode."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.5})

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {SETPOINT: 22.5}}
        )
        assert entity.target_temperature == 22.5

    async def test_a_mode_sent_with_the_setpoint_travels_with_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The action takes both, and a caller sending both means them in one write."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_set_temperature(
            **{ATTR_TEMPERATURE: 22.5, ATTR_HVAC_MODE: HVACMode.HEAT}
        )

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {SETPOINT: 22.5, POWER: True, MODE: "Heat"}}
        )

    async def test_a_setpoint_sent_with_off_stops_the_device(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Off is not a value of the mode parameter on every device.

        It goes through the one path that knows which parameter can do it,
        rather than being written as a mode the device may not have.
        """
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_set_temperature(
            **{ATTR_TEMPERATURE: 22.5, ATTR_HVAC_MODE: HVACMode.OFF}
        )

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )

    async def test_set_fan_mode_writes_the_mapped_number(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The fan speed name becomes the number the device expects."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_set_fan_mode("medium")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {FAN: 3}}
        )
        assert entity.fan_mode == "medium"

    async def test_turn_on_picks_a_running_mode(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Turning on must choose a mode that is not off."""
        entity = _entity(coordinator, hass, report={POWER: False})

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, MODE: "Cool"}}
        )
