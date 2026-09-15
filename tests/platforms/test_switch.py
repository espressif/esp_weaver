# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven switch platform."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest

from custom_components.esp_weaver.helpers.signals import (
    signal_binding_discovered,
    signal_params_update,
)
from custom_components.esp_weaver.iot.managers.property_manager import WriteOutcome
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.switch import ESPWeaverSwitch, async_setup_entry
from tests.conftest import (
    TEST_DEVICE_NAME,
    TEST_NODE_ID,
    AddedEntities,
    create_mock_config_entry,
    record_state_writes,
    state_writes,
)

DEVICE = "Threshold"
PARAM = "threshold_alarm_enabled"


def _binding(**overrides: Any) -> EntityBinding:
    """Build a writable on/off binding."""
    spec = AttributeSpec(
        name="state",
        param=PARAM,
        data_type="bool",
        properties=("read", "write"),
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.sensor",
        "platform": "switch",
        "name": "Threshold Alarm",
        "key": "threshold_alarm_enabled",
        "attributes": {
            "state": ParamBinding(device_name=DEVICE, attribute="state", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


class TestSwitchFromBinding:
    """Building a switch out of a resolved binding."""

    def test_takes_its_identity_from_the_binding(self, coordinator: MagicMock) -> None:
        """unique_id comes from the profile key, not from the display name."""
        entity = ESPWeaverSwitch(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.unique_id == (f"{TEST_NODE_ID}_threshold_alarm_enabled")
        assert entity.name == "Threshold Alarm"
        assert entity.is_on is None

    def test_adopts_the_reported_state(self, coordinator: MagicMock) -> None:
        """The report a node already published becomes the starting state."""
        entity = ESPWeaverSwitch(
            coordinator, _binding(), TEST_DEVICE_NAME, report={PARAM: True}
        )

        assert entity.is_on is True

    def test_shows_the_profile_s_read_only_detail(self, coordinator: MagicMock) -> None:
        """Every platform shows extra_attributes, not only the composite ones."""
        detail = AttributeSpec(
            name="reason", param="Reason", data_type="string", properties=("read",)
        )
        binding = _binding(
            extra_attributes={
                "reason": ParamBinding(
                    device_name=DEVICE, attribute="reason", spec=detail
                )
            }
        )
        entity = ESPWeaverSwitch(
            coordinator, binding, TEST_DEVICE_NAME, report={PARAM: True}
        )
        assert entity.extra_state_attributes == {}
        write = record_state_writes(entity)

        entity._handle_params_update({DEVICE: {"Reason": "manual"}})

        assert entity.extra_state_attributes == {"reason": "manual"}
        write.assert_called_once()

    def test_translates_entity_category(self, coordinator: MagicMock) -> None:
        """A declared entity_category reaches Home Assistant."""
        entity = ESPWeaverSwitch(
            coordinator, _binding(entity_category="config"), TEST_DEVICE_NAME
        )

        assert entity.entity_category == EntityCategory.CONFIG

    def test_ignores_an_unknown_entity_category(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bad entity_category is dropped with a warning, not raised."""
        entity = ESPWeaverSwitch(
            coordinator, _binding(entity_category="nonsense"), TEST_DEVICE_NAME
        )

        assert entity.entity_category is None
        assert "not a valid EntityCategory" in caplog.text

    def test_ignores_an_unknown_device_class(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bad device_class is dropped with a warning, not raised."""
        entity = ESPWeaverSwitch(
            coordinator, _binding(device_class="nonsense"), TEST_DEVICE_NAME
        )

        assert entity.device_class is None
        assert "not a valid SwitchDeviceClass" in caplog.text


class TestSwitchWrites:
    """Turning the switch on and off."""

    async def test_addresses_the_device_the_binding_names(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The payload names the device instance, nothing is guessed."""
        entity = ESPWeaverSwitch(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "switch.test"

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: True}}
        )
        assert entity.is_on is True

    async def test_turn_off_sends_false(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Turning off writes the inverse value."""
        entity = ESPWeaverSwitch(
            coordinator, _binding(), TEST_DEVICE_NAME, report={PARAM: True}
        )
        entity.hass = hass
        entity.entity_id = "switch.test"

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: False}}
        )
        assert entity.is_on is False

    async def test_read_only_parameter_refuses_the_write(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A parameter the device will not accept raises instead of lying."""
        spec = AttributeSpec(name="state", param=PARAM, properties=("read",))
        binding = _binding(
            attributes={
                "state": ParamBinding(device_name=DEVICE, attribute="state", spec=spec)
            }
        )
        entity = ESPWeaverSwitch(coordinator, binding, TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "switch.test"

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "write_not_supported"

        coordinator.api.set_params.assert_not_called()
        # The node and the profile disagree; the node is asked again.
        coordinator.async_resync_structure.assert_awaited_once()

    async def test_a_refused_write_is_reported_and_undone(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A device that answers no must not leave the switch showing yes."""
        entity = ESPWeaverSwitch(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "switch.test"
        coordinator.api.set_params = AsyncMock(return_value=WriteOutcome.REFUSED)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "write_refused"

        coordinator.async_refresh.assert_awaited_once()

    async def test_an_unreachable_device_is_not_called_a_refusal(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The device layer answers UNREACHABLE for a node that is offline.

        It never raises for one, so this, not an exception, is the path a
        switched-off device takes.
        """
        entity = ESPWeaverSwitch(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "switch.test"
        coordinator.api.set_params = AsyncMock(return_value=WriteOutcome.UNREACHABLE)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "write_failed"

        coordinator.async_refresh.assert_awaited_once()

    async def test_a_transport_error_is_reported_as_homeassistant_error(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Anything that still escapes the device layer is wrapped, not leaked."""
        entity = ESPWeaverSwitch(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "switch.test"
        coordinator.api.set_params = AsyncMock(side_effect=OSError("down"))

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "write_failed"

        coordinator.async_refresh.assert_awaited_once()


class TestSwitchReads:
    """Picking the switch's own parameter out of a device report."""

    def _entity(self, hass: HomeAssistant, coordinator: MagicMock) -> ESPWeaverSwitch:
        entity = ESPWeaverSwitch(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "switch.test"
        record_state_writes(entity)
        return entity

    def test_applies_a_matching_report(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A report naming this device and parameter updates the state."""
        entity = self._entity(hass, coordinator)

        entity._handle_params_update({DEVICE: {PARAM: True}})

        assert entity.is_on is True
        state_writes(entity).assert_called_once()

    def test_ignores_another_device_instance(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The same parameter name on a different device is not ours."""
        entity = self._entity(hass, coordinator)

        entity._handle_params_update({"Other": {PARAM: True}})

        assert entity.is_on is None
        state_writes(entity).assert_not_called()

    async def test_ignores_another_node(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Reports from other nodes never reach this entity's signal."""
        entity = self._entity(hass, coordinator)
        await entity.async_added_to_hass()

        async_dispatcher_send(
            hass, signal_params_update("other"), {DEVICE: {PARAM: True}}
        )
        await hass.async_block_till_done()
        assert entity.is_on is None

        async_dispatcher_send(
            hass, signal_params_update(TEST_NODE_ID), {DEVICE: {PARAM: True}}
        )
        await hass.async_block_till_done()
        assert entity.is_on is True

    def test_state_read_before_the_first_report_does_not_stick(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A class attribute shadowing ToggleEntity's ``_attr_is_on`` breaks the cache."""
        entity = self._entity(hass, coordinator)

        assert entity.is_on is None
        entity._handle_params_update({DEVICE: {PARAM: True}})
        assert entity.is_on is True
        entity._handle_params_update({DEVICE: {PARAM: False}})
        assert entity.is_on is False

    def test_unchanged_value_does_not_rewrite_state(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Repeating the current value is not an update."""
        entity = self._entity(hass, coordinator)
        entity._attr_is_on = True

        entity._handle_params_update({DEVICE: {PARAM: True}})

        state_writes(entity).assert_not_called()


class TestSwitchDiscovery:
    """The platform's binding discovery listener."""

    async def _setup(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> list[Any]:
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = coordinator
        entry.title = TEST_DEVICE_NAME
        added = AddedEntities()

        await async_setup_entry(hass, entry, added)
        return added

    async def test_creates_a_switch_for_a_switch_binding(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A switch binding for this node produces one entity."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass, signal_binding_discovered(TEST_NODE_ID), _binding(), {PARAM: True}
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].is_on is True

    async def test_ignores_bindings_for_other_platforms(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A sensor binding is not a switch."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _binding(platform="sensor"),
            {},
        )
        await hass.async_block_till_done()

        assert added == []

    async def test_does_not_create_the_same_switch_twice(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Rediscovery after a reconnect must not duplicate entities."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        for _ in range(2):
            async_dispatcher_send(
                hass, signal_binding_discovered(TEST_NODE_ID), _binding(), {}
            )
            await hass.async_block_till_done()

        assert len(added) == 1
