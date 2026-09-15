# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven valve platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.valve import ValveDeviceClass, ValveEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.valve import ESPWeaverValve
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Valve"
POSITION = "Valve Position"


def _binding(**overrides: Any) -> EntityBinding:
    """Build a valve binding driven by one position parameter."""
    spec = AttributeSpec(
        name="position",
        param=POSITION,
        data_type="int",
        properties=("read", "write"),
        bounds={"min": 0, "max": 100, "step": 1},
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.valve",
        "platform": "valve",
        "name": "Valve",
        "key": "valve",
        "device_class": "water",
        "attributes": {
            "position": ParamBinding(
                device_name=DEVICE, attribute="position", spec=spec
            )
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverValve:
    entity = ESPWeaverValve(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "valve.test"
    return entity


class TestValveCapabilities:
    """What the valve offers and what it says it is."""

    def test_offers_both_ends_and_the_slider(self, coordinator: MagicMock) -> None:
        """A position parameter is enough for all three controls."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            ValveEntityFeature.OPEN
            | ValveEntityFeature.CLOSE
            | ValveEntityFeature.SET_POSITION
        )
        assert entity.reports_position is True

    def test_takes_the_device_class_from_the_profile(
        self, coordinator: MagicMock
    ) -> None:
        """A water valve should not be drawn as a gas one."""
        entity = _entity(coordinator)

        assert entity.device_class == ValveDeviceClass.WATER

    def test_a_read_only_position_is_shown_but_not_driven(
        self, coordinator: MagicMock
    ) -> None:
        """A valve that only says where it is still says where it is.

        What it does not get is three controls that write nothing: the card
        would move and the valve would not.
        """
        spec = AttributeSpec(
            name="position",
            param=POSITION,
            data_type="int",
            properties=("read",),
            bounds={"min": 0, "max": 100},
        )
        entity = _entity(
            coordinator,
            attributes={
                "position": ParamBinding(
                    device_name=DEVICE, attribute="position", spec=spec
                )
            },
        )

        assert entity.reports_position is True
        assert entity.supported_features == ValveEntityFeature(0)


class TestValveReads:
    """Turning a report into a position."""

    def test_position_is_unknown_before_the_device_speaks(
        self, coordinator: MagicMock
    ) -> None:
        """Nothing reported is not the same as closed."""
        entity = _entity(coordinator)

        assert entity.current_valve_position is None

    def test_adopts_the_reported_position(self, coordinator: MagicMock) -> None:
        """Half open reads as half open."""
        entity = _entity(coordinator, report={POSITION: 50})

        assert entity.current_valve_position == 50


class TestValveWrites:
    """Opening, closing and positioning the valve."""

    async def test_open_writes_the_far_end(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Opening is a write to the top of the travel."""
        entity = _entity(coordinator, hass)

        await entity.async_open_valve()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POSITION: 100}}
        )
        assert entity.current_valve_position == 100

    async def test_close_writes_the_near_end(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Closing is a write to the bottom of the travel."""
        entity = _entity(coordinator, hass, report={POSITION: 100})

        await entity.async_close_valve()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POSITION: 0}}
        )

    async def test_set_position_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The slider goes straight to the parameter."""
        entity = _entity(coordinator, hass)

        await entity.async_set_valve_position(30)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POSITION: 30}}
        )
