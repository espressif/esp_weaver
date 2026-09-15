# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver base entity class."""

import logging
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.vacuum import VacuumActivity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.esp_weaver.entity import (
    ESPWeaverBaseEntity,
    command_state,
    reported_member,
    resolve_commands,
)
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.profiles import DeviceIdentity
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.switch import ESPWeaverSwitch

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, record_state_writes

DEVICE = "Switch"
PARAM = "Power"


class TestESPWeaverBaseEntity:
    """Test ESPWeaverBaseEntity class."""

    def test_entity_initialization(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic entity initialization."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
            device_name=TEST_DEVICE_NAME,
        )

        assert entity._node_id == TEST_NODE_ID
        assert entity._device_name == TEST_DEVICE_NAME
        assert entity._attr_has_entity_name is True
        assert entity.should_poll is False

    def test_entity_unique_id(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity unique_id generation."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        expected_unique_id = f"{TEST_NODE_ID}_test_key"
        assert entity._attr_unique_id == expected_unique_id

    def test_entity_device_info(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Identifiers, name, serial number and the product's vendor and label.

        Model id and firmware version are the node's own, the coordinator's
        to write from its config; an entity naming them here would undo that.
        """
        mock_coordinator.identity = DeviceIdentity(
            serial_number=TEST_NODE_ID,
            manufacturer="acme",
            model="Acme Bulb",
        )
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
            device_name=TEST_DEVICE_NAME,
        )

        device_info = entity._attr_device_info
        assert device_info is not None
        assert (DOMAIN, TEST_NODE_ID) in device_info["identifiers"]
        assert device_info["name"] == TEST_DEVICE_NAME
        assert device_info["serial_number"] == TEST_NODE_ID
        assert device_info["manufacturer"] == "acme"
        assert device_info["model"] == "Acme Bulb"
        assert not {"model_id", "sw_version"} & device_info.keys()

    def test_entity_uses_coordinator_device_name(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity uses coordinator's device_name when not provided."""
        mock_coordinator.device_name = "Coordinator Device Name"

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        assert entity._device_name == "Coordinator Device Name"

    def test_entity_coordinator_relationship(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity coordinator is properly set."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        assert entity.coordinator is mock_coordinator


class TestStateAttributes:
    """State attributes only carry what the profile declares."""

    @staticmethod
    def _entity(coordinator: MagicMock, **overrides: Any) -> ESPWeaverSwitch:
        """Build a switch, the plainest entity there is."""
        spec = AttributeSpec(
            name="state",
            param=PARAM,
            data_type="bool",
            properties=("read", "write"),
        )
        defaults: dict[str, Any] = {
            "node_id": TEST_NODE_ID,
            "device_name": DEVICE,
            "device_type": "esp.device.switch",
            "platform": "switch",
            "name": "Switch",
            "key": "switch",
            "attributes": {
                "state": ParamBinding(device_name=DEVICE, attribute="state", spec=spec)
            },
        }
        binding = EntityBinding(**{**defaults, **overrides})
        return ESPWeaverSwitch(coordinator, binding, TEST_DEVICE_NAME)

    def test_nothing_is_claimed_before_the_device_has_spoken(
        self, mock_coordinator: MagicMock
    ) -> None:
        """No extra attribute is invented for a report that never came."""
        entity = self._entity(mock_coordinator)

        assert entity.extra_state_attributes == {}

    def test_a_report_only_fills_declared_extras(
        self, mock_coordinator: MagicMock
    ) -> None:
        """A profile without extra attributes never grows any."""
        entity = self._entity(mock_coordinator)

        entity._apply_report({PARAM: True})

        assert entity.extra_state_attributes == {}


class TestNaming:
    """The name follows the user's language; the entity_id does not."""

    # What the profile calls the entity, and what a zh user would see instead.
    PROFILE_NAME = "Ceiling Light"
    TRANSLATED_NAME = "吸顶灯"
    TRANSLATION_KEY = f"component.{DOMAIN}.entity.switch.switch.name"

    @staticmethod
    def _entity(
        coordinator: MagicMock, translations: dict[str, str] | None = None
    ) -> ESPWeaverSwitch:
        """Build a switch that believes the given translations are loaded."""
        entity = TestStateAttributes._entity(coordinator, name=TestNaming.PROFILE_NAME)
        platform_data = MagicMock()
        platform_data.platform_name = DOMAIN
        platform_data.domain = "switch"
        platform_data.platform_translations = translations or {}
        entity.platform_data = platform_data
        return entity

    def test_an_untranslated_entity_is_called_what_the_profile_calls_it(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Home Assistant's own fallback here is the device class name."""
        entity = self._entity(mock_coordinator)

        assert entity.name == self.PROFILE_NAME

    def test_a_translated_entity_is_called_by_its_translation(
        self, mock_coordinator: MagicMock
    ) -> None:
        """The profile name is the fallback, not the answer."""
        entity = self._entity(
            mock_coordinator, {self.TRANSLATION_KEY: self.TRANSLATED_NAME}
        )

        assert entity.name == self.TRANSLATED_NAME

    def test_the_entity_id_is_the_profile_name_either_way(
        self, mock_coordinator: MagicMock
    ) -> None:
        """A translated name must not reach the entity_id."""
        translated = self._entity(
            mock_coordinator, {self.TRANSLATION_KEY: self.TRANSLATED_NAME}
        )

        assert self._entity(mock_coordinator).suggested_object_id == self.PROFILE_NAME
        assert translated.suggested_object_id == self.PROFILE_NAME


class TestEntityAvailability:
    """Test entity availability logic."""

    def test_entity_available_when_coordinator_available(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity is available when coordinator is available."""
        mock_coordinator.last_update_success = True
        mock_coordinator.is_available = True

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        assert entity.available is True

    def test_entity_unavailable_when_device_offline(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity is unavailable when device is offline."""
        mock_coordinator.last_update_success = True
        mock_coordinator.is_available = False

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        assert entity.available is False

    def test_entity_availability_ignores_the_refresh_outcome(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Only the coordinator's own verdict counts, not whether a refresh raised."""
        mock_coordinator.last_update_success = False
        mock_coordinator.is_available = True

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        assert entity.available is True


class TestEntityCoordinatorUpdate:
    """Test entity coordinator update handling."""

    async def test_handle_coordinator_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test _handle_coordinator_update calls async_write_ha_state."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )
        entity.hass = hass
        write = record_state_writes(entity)

        entity._handle_coordinator_update()

        write.assert_called_once()


def _activity_binding(
    options: tuple[str, ...] | None, writable: bool = True
) -> EntityBinding:
    """Build a binding whose whole state is one enumerated ``activity``."""
    spec = AttributeSpec(
        name="activity",
        param="Mode",
        data_type="string",
        properties=("read", "write") if writable else ("read",),
        options=options,
    )
    return EntityBinding(
        node_id=TEST_NODE_ID,
        device_name="Vacuum",
        device_type="esp.device.vacuum",
        platform="vacuum",
        name="Vacuum",
        key="vacuum",
        attributes={
            "activity": ParamBinding(
                device_name="Vacuum", attribute="activity", spec=spec
            )
        },
    )


WANTED = {"start": "cleaning", "stop": "idle", "return_to_base": "returning"}


class TestResolveCommands:
    """Which of the commands a platform offers this device can take."""

    def test_keeps_only_the_states_the_device_listed(self) -> None:
        """A button for a state the device never named would write garbage."""
        binding = _activity_binding(("cleaning", "idle"))

        assert resolve_commands(binding, "activity", WANTED) == {
            "start": "cleaning",
            "stop": "idle",
        }

    def test_an_unenumerated_parameter_takes_everything(self) -> None:
        """No list means no evidence against any state."""
        binding = _activity_binding(None)

        assert resolve_commands(binding, "activity", WANTED) == WANTED

    def test_a_read_only_parameter_takes_nothing(self) -> None:
        """Nothing can be commanded through a parameter that cannot be written."""
        binding = _activity_binding(("cleaning", "idle"), writable=False)

        assert resolve_commands(binding, "activity", WANTED) == {}

    def test_an_absent_attribute_takes_nothing(self) -> None:
        """A device without the attribute has nothing to command."""
        binding = _activity_binding(("cleaning",))

        assert resolve_commands(binding, "fan_speed", WANTED) == {}


class TestReportedMember:
    """One reported string against the platform's enum."""

    def test_a_listed_value_is_the_member(self) -> None:
        """The plain case."""
        assert (
            reported_member(VacuumActivity, "cleaning", TEST_NODE_ID, "Vacuum")
            is VacuumActivity.CLEANING
        )

    @pytest.mark.parametrize("value", [None, 3, True])
    def test_a_non_string_is_unknown(self, value: object) -> None:
        """Only text can name a state."""
        assert reported_member(VacuumActivity, value, TEST_NODE_ID, "Vacuum") is None

    def test_an_unlisted_value_is_unknown_and_blamed_on_the_profile(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The profile is where a spelling difference gets fixed."""
        caplog.set_level(logging.WARNING)

        assert (
            reported_member(VacuumActivity, "Sweeping", TEST_NODE_ID, "Vacuum") is None
        )

        assert "'Sweeping'" in caplog.text
        assert "the profile needs a map" in caplog.text


class TestCommandState:
    """The state a command asks for."""

    def test_an_accepted_command_names_its_state(self) -> None:
        """The plain case."""
        assert command_state(WANTED, "start", TEST_NODE_ID, "Vacuum") == "cleaning"

    def test_a_command_the_device_never_offered_is_refused(self) -> None:
        """A request that quietly did nothing would look like a dead device."""
        with pytest.raises(ServiceValidationError) as err:
            command_state({"stop": "idle"}, "start", TEST_NODE_ID, "Vacuum")

        assert err.value.translation_key == "command_not_accepted"
        assert err.value.translation_placeholders == {
            "entity": "Vacuum",
            "node_id": TEST_NODE_ID,
            "command": "start",
            "accepted": "idle",
        }

    def test_a_device_that_accepts_nothing_says_so(self) -> None:
        """An empty list still has to read as a sentence."""
        with pytest.raises(ServiceValidationError) as err:
            command_state({}, "start", TEST_NODE_ID, "Vacuum")

        assert err.value.translation_placeholders is not None
        assert err.value.translation_placeholders["accepted"] == "nothing"
