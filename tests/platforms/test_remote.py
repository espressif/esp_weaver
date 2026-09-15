# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven remote platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.remote import RemoteEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.remote import ESPWeaverRemote
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Remote"
POWER = "Power"
ACTIVITY = "Activity"

ACTIVITIES = ("Movie", "Music", "Gaming")


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(
    activity: bool = True, activities: tuple[str, ...] = ACTIVITIES
) -> dict[str, ParamBinding]:
    """Build the attribute set of a remote with the given capabilities."""
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
    if activity:
        attributes["current_activity"] = _param(
            "current_activity",
            AttributeSpec(
                name="current_activity",
                param=ACTIVITY,
                data_type="string",
                properties=("read", "write"),
                options=activities or None,
            ),
        )
    return attributes


def _binding(**overrides: Any) -> EntityBinding:
    """Build a remote binding with every capability the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.remote-control",
        "platform": "remote",
        "name": "Remote",
        "key": "remote",
        "attributes": _attributes(),
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverRemote:
    entity = ESPWeaverRemote(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "remote.test"
    return entity


class TestRemoteCapabilities:
    """What the remote offers depends on what the device reported."""

    def test_scenes_come_from_the_reported_values(self, coordinator: MagicMock) -> None:
        """The activity list offers what the firmware listed, nothing more."""
        entity = _entity(coordinator)

        assert entity.supported_features == RemoteEntityFeature.ACTIVITY
        assert entity.activity_list == list(ACTIVITIES)

    def test_a_remote_without_scenes_is_only_a_switch(
        self, coordinator: MagicMock
    ) -> None:
        """Absent optional parameters must not become dead controls."""
        entity = _entity(coordinator, attributes=_attributes(activity=False))

        assert entity.supported_features == RemoteEntityFeature(0)
        assert entity.activity_list is None

    def test_scenes_without_values_are_not_offered(
        self, coordinator: MagicMock, caplog: Any
    ) -> None:
        """A picker with nothing in it is worse than no picker."""
        entity = _entity(coordinator, attributes=_attributes(activities=()))

        assert entity.supported_features == RemoteEntityFeature(0)
        assert "no activities" in caplog.text


class TestRemoteCommands:
    """The three command actions Home Assistant registers regardless."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (
                lambda entity: entity.async_send_command(["KEY_UP"]),
                "remote_no_commands",
            ),
            (lambda entity: entity.async_learn_command(), "remote_cannot_learn"),
            (
                lambda entity: entity.async_delete_command(command=["x"]),
                "remote_no_stored_commands",
            ),
        ],
    )
    async def test_a_command_action_says_why_it_cannot(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        call: Any,
        expected: str,
    ) -> None:
        """Reachable with no feature to gate them on; must not traceback."""
        entity = _entity(coordinator, hass)

        with pytest.raises(HomeAssistantError) as err:
            await call(entity)

        assert err.value.translation_key == expected


class TestRemoteReads:
    """Turning a report into state."""

    def test_adopts_power_and_scene(self, coordinator: MagicMock) -> None:
        """One report carries both."""
        entity = _entity(coordinator, report={POWER: True, ACTIVITY: "Music"})

        assert entity.is_on is True
        assert entity.current_activity == "Music"

    def test_state_is_unknown_before_the_device_speaks(
        self, coordinator: MagicMock
    ) -> None:
        """Off is a report, not the absence of one."""
        entity = _entity(coordinator)

        assert entity.is_on is None


class TestRemoteWrites:
    """Switching the remote and running a scene."""

    async def test_turn_on_with_an_activity_writes_one_payload(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Both travel together so the remote never runs the previous scene."""
        entity = _entity(coordinator, hass, report={POWER: False, ACTIVITY: "Movie"})

        await entity.async_turn_on(activity="Gaming")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, ACTIVITY: "Gaming"}}
        )
        assert entity.current_activity == "Gaming"

    async def test_turn_on_without_an_activity_writes_power_alone(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Turning it on must not disturb the scene it will resume."""
        entity = _entity(coordinator, hass, report={POWER: False, ACTIVITY: "Movie"})

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )

    async def test_turn_off_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Switching off leaves the scene where it was."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.is_on is False
