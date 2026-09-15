# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven humidifier platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.esp_weaver.humidifier import ESPWeaverHumidifier
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Humidifier"
POWER = "Power"
TARGET = "Target Humidity"
MODE = "Mode"
CURRENT = "Humidity"

MODES = ("auto", "sleep", "boost")


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(
    target: bool = True, mode: bool = True, modes: tuple[str, ...] = MODES
) -> dict[str, ParamBinding]:
    """Build the attribute set of a humidifier with the given capabilities."""
    attributes = {
        "state": _param(
            "state",
            AttributeSpec(
                name="state",
                param=POWER,
                data_type="bool",
                properties=("read", "write"),
            ),
        ),
        "current_humidity": _param(
            "current_humidity",
            AttributeSpec(
                name="current_humidity",
                param=CURRENT,
                data_type="int",
                properties=("read",),
            ),
        ),
    }
    if target:
        attributes["target_humidity"] = _param(
            "target_humidity",
            AttributeSpec(
                name="target_humidity",
                param=TARGET,
                data_type="int",
                properties=("read", "write"),
                bounds={"min": 30, "max": 80, "step": 1},
            ),
        )
    if mode:
        attributes["mode"] = _param(
            "mode",
            AttributeSpec(
                name="mode",
                param=MODE,
                data_type="string",
                properties=("read", "write"),
                options=modes or None,
            ),
        )
    return attributes


def _binding(**overrides: Any) -> EntityBinding:
    """Build a humidifier binding with every capability the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.humidifier",
        "platform": "humidifier",
        "name": "Humidifier",
        "key": "humidifier",
        "device_class": "humidifier",
        "attributes": _attributes(),
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverHumidifier:
    entity = ESPWeaverHumidifier(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "humidifier.test"
    return entity


class TestHumidifierCapabilities:
    """What the humidifier offers depends on what the device reported."""

    def test_power_only_device_has_no_setpoint_or_modes(
        self, coordinator: MagicMock
    ) -> None:
        """Absent optional parameters must not become dead controls."""
        entity = _entity(coordinator, attributes=_attributes(target=False, mode=False))

        assert entity.supported_features == HumidifierEntityFeature(0)
        assert entity.target_humidity is None
        assert entity.mode is None

    def test_modes_come_from_the_reported_values(self, coordinator: MagicMock) -> None:
        """The picker offers what the firmware listed, nothing more."""
        entity = _entity(coordinator)

        assert entity.supported_features == HumidifierEntityFeature.MODES
        assert entity.available_modes == list(MODES)

    def test_a_mode_without_values_is_not_offered(self, coordinator: MagicMock) -> None:
        """Home Assistant needs the list alongside the feature to draw a picker."""
        entity = _entity(coordinator, attributes=_attributes(modes=()))

        assert entity.supported_features == HumidifierEntityFeature(0)
        assert entity.available_modes is None

    def test_setpoint_range_follows_the_device(self, coordinator: MagicMock) -> None:
        """A device that will not go below 30 should not be asked to."""
        entity = _entity(coordinator)

        assert entity.min_humidity == 30
        assert entity.max_humidity == 80

    def test_takes_the_device_class_from_the_profile(
        self, coordinator: MagicMock
    ) -> None:
        """A humidifier and a dehumidifier read differently in the UI."""
        entity = _entity(coordinator)

        assert entity.device_class == HumidifierDeviceClass.HUMIDIFIER


class TestHumidifierReads:
    """Turning a report into state."""

    def test_adopts_everything_reported(self, coordinator: MagicMock) -> None:
        """One report carries power, both humidities and the mode."""
        entity = _entity(
            coordinator,
            report={POWER: True, TARGET: 55, CURRENT: 41, MODE: "sleep"},
        )

        assert entity.is_on is True
        assert entity.target_humidity == 55
        assert entity.current_humidity == 41
        assert entity.mode == "sleep"

    def test_state_is_unknown_before_the_device_speaks(
        self, coordinator: MagicMock
    ) -> None:
        """Off is a report, not the absence of one."""
        entity = _entity(coordinator)

        assert entity.is_on is None


class TestHumidifierWrites:
    """Starting, stopping and adjusting the humidifier."""

    async def test_turn_on_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Starting must not disturb the setpoint it will resume at."""
        entity = _entity(coordinator, hass, report={POWER: False, TARGET: 55})

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )
        assert entity.is_on is True

    async def test_turn_off_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Stopping is the same write the other way."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )

    async def test_set_humidity_writes_the_setpoint(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The slider goes straight to the parameter."""
        entity = _entity(coordinator, hass)

        await entity.async_set_humidity(60)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {TARGET: 60}}
        )
        assert entity.target_humidity == 60

    async def test_set_mode_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Picking a mode writes the mode and leaves power alone."""
        entity = _entity(coordinator, hass, report={POWER: True, MODE: "auto"})

        await entity.async_set_mode("boost")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {MODE: "boost"}}
        )
        assert entity.mode == "boost"

    async def test_a_setpoint_the_device_lacks_is_refused_and_not_shown(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A humidity write to a device with no such parameter is refused, not shown."""
        entity = _entity(coordinator, hass, attributes=_attributes(target=False))

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_set_humidity(60)
        assert err.value.translation_key == "write_not_supported"

        coordinator.api.set_params.assert_not_called()
        assert entity.target_humidity is None
