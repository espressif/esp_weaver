# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven lawn mower platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.esp_weaver.lawn_mower import ESPWeaverLawnMower
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Lawn Mower"
ACTIVITY = "Activity"

ACTIVITIES = ("mowing", "docked", "paused", "returning", "error")


def _binding(
    activities: tuple[str, ...] = ACTIVITIES, **overrides: Any
) -> EntityBinding:
    """Build a mower binding whose activity the device also accepts."""
    spec = AttributeSpec(
        name="activity",
        param=ACTIVITY,
        data_type="string",
        properties=("read", "write"),
        options=activities or None,
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.lawn-mower",
        "platform": "lawn_mower",
        "name": "Lawn Mower",
        "key": "lawn_mower",
        "attributes": {
            "activity": ParamBinding(
                device_name=DEVICE, attribute="activity", spec=spec
            )
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    activities: tuple[str, ...] = ACTIVITIES,
) -> ESPWeaverLawnMower:
    entity = ESPWeaverLawnMower(
        coordinator, _binding(activities), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "lawn_mower.test"
    return entity


class TestLawnMowerCapabilities:
    """Which buttons appear follows the values the device listed."""

    def test_all_three_buttons_when_the_device_named_all_three_states(
        self, coordinator: MagicMock
    ) -> None:
        """Mowing, pausing and docking each need their own value."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            LawnMowerEntityFeature.START_MOWING
            | LawnMowerEntityFeature.PAUSE
            | LawnMowerEntityFeature.DOCK
        )

    def test_a_state_the_device_never_named_gets_no_button(
        self, coordinator: MagicMock
    ) -> None:
        """A mower that cannot pause should not be shown a pause button."""
        entity = _entity(coordinator, activities=("mowing", "docked"))

        assert not entity.supported_features & LawnMowerEntityFeature.PAUSE


class TestLawnMowerReads:
    """Turning a report into an activity."""

    def test_adopts_the_reported_activity(self, coordinator: MagicMock) -> None:
        """What the device says it is doing is what the entity shows."""
        entity = _entity(coordinator, report={ACTIVITY: "mowing"})

        assert entity.activity == LawnMowerActivity.MOWING

    def test_an_unknown_activity_is_dropped(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A state Home Assistant has no name for would be an invalid state."""
        entity = _entity(coordinator, report={ACTIVITY: "edging"})

        assert entity.activity is None
        assert "not one of" in caplog.text


class TestLawnMowerCommands:
    """Every button is a write to the activity parameter."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            ("async_start_mowing", "mowing"),
            ("async_pause", "paused"),
            ("async_dock", "docked"),
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
        entity = _entity(coordinator, hass, report={ACTIVITY: "docked"})

        await getattr(entity, call)()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {ACTIVITY: expected}}
        )
        assert entity.activity == LawnMowerActivity(expected)

    async def test_a_command_the_device_rejects_is_not_sent(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
    ) -> None:
        """Writing a state the device never listed would only be refused.

        Said out loud rather than dropped: a request that quietly does nothing
        looks exactly like a device that is not listening.
        """
        entity = _entity(coordinator, hass, activities=("mowing", "docked"))

        with pytest.raises(ServiceValidationError) as err:
            await entity.async_pause()

        assert err.value.translation_key == "command_not_accepted"
        assert err.value.translation_placeholders is not None
        assert err.value.translation_placeholders["command"] == "pause"

        coordinator.api.set_params.assert_not_awaited()
