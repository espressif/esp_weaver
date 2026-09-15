# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven binary sensor platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest

from custom_components.esp_weaver.binary_sensor import (
    ATTR_BINARY_SENSOR_TYPE,
    ESPWeaverBinarySensor,
    async_setup_entry,
)
from custom_components.esp_weaver.helpers.signals import (
    signal_binding_discovered,
    signal_params_update,
)
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import (
    TEST_DEVICE_NAME,
    TEST_NODE_ID,
    AddedEntities,
    create_mock_config_entry,
    record_state_writes,
    state_writes,
)

DEVICE = "Binary Sensor"
STATE_PARAM = "State"
CLASS_PARAM = "device_class"

# The mapping the shipped binary_sensor.yaml declares: the firmware's "touch" is
# Home Assistant's "occupancy", everything else passes through.
CLASS_MAP = {
    "door": "door",
    "plug": "plug",
    "motion": "motion",
    "vibration": "vibration",
    "touch": "occupancy",
}


def _param(name: str, param: str, **spec_kwargs: Any) -> ParamBinding:
    """Build one bound parameter."""
    return ParamBinding(
        device_name=DEVICE,
        attribute=name,
        spec=AttributeSpec(name=name, param=param, **spec_kwargs),
    )


def _binding(**overrides: Any) -> EntityBinding:
    """Build a binding shaped like the shipped binary sensor profile."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.binary-sensor",
        "platform": "binary_sensor",
        "name": "Binary Sensor",
        "key": "binary_sensor",
        "device_class": "door",
        "attributes": {
            "state": _param("state", STATE_PARAM, data_type="bool"),
            "device_class": _param(
                "device_class",
                CLASS_PARAM,
                data_type="string",
                properties=("read", "write"),
                value_map=dict(CLASS_MAP),
            ),
        },
        "extra_attributes": {
            "debounce_time": _param("debounce_time", "debounce_time", data_type="int"),
            "report_interval": _param(
                "report_interval", "report_interval", data_type="int"
            ),
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _report(entity: ESPWeaverBinarySensor, **params: Any) -> None:
    """Feed the entity one device report."""
    entity._handle_params_update({DEVICE: params})


class TestBinarySensorFromBinding:
    """Building a binary sensor out of a resolved binding."""

    def test_takes_its_identity_from_the_binding(self, coordinator: MagicMock) -> None:
        """unique_id comes from the profile key, not from the display name."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.unique_id == f"{TEST_NODE_ID}_binary_sensor"
        assert entity.name == "Binary Sensor"
        assert entity.is_on is None

    def test_uses_the_profile_key_as_translation_key(
        self, coordinator: MagicMock
    ) -> None:
        """Translations are keyed on the profile key, so it must be set."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.translation_key == "binary_sensor"

    def test_adopts_the_reported_state(self, coordinator: MagicMock) -> None:
        """The report a node already published becomes the starting state."""
        entity = ESPWeaverBinarySensor(
            coordinator, _binding(), TEST_DEVICE_NAME, report={STATE_PARAM: True}
        )

        assert entity.is_on is True

    def test_declared_device_class_is_used(self, coordinator: MagicMock) -> None:
        """Before the device reports one, the profile's device_class applies."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.device_class == BinarySensorDeviceClass.DOOR

    def test_unknown_device_class_is_dropped(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bad device_class is logged and skipped rather than raised."""
        entity = ESPWeaverBinarySensor(
            coordinator, _binding(device_class="nonsense"), TEST_DEVICE_NAME
        )

        assert entity.device_class is None
        assert "not a valid BinarySensorDeviceClass" in caplog.text


class TestBinarySensorReports:
    """Reacting to what the device reports."""

    def test_state_follows_the_primary_parameter(self, coordinator: MagicMock) -> None:
        """The state parameter drives is_on."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        record_state_writes(entity)

        _report(entity, **{STATE_PARAM: True})

        assert entity.is_on is True
        state_writes(entity).assert_called_once()

    def test_unchanged_state_writes_nothing(self, coordinator: MagicMock) -> None:
        """A report that changes nothing must not wake Home Assistant up."""
        entity = ESPWeaverBinarySensor(
            coordinator, _binding(), TEST_DEVICE_NAME, report={STATE_PARAM: True}
        )
        record_state_writes(entity)

        _report(entity, **{STATE_PARAM: True})

        state_writes(entity).assert_not_called()

    async def test_a_report_for_another_node_is_ignored(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Each node has its own signal, so another node's report never arrives."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        record_state_writes(entity)
        await entity.async_added_to_hass()

        async_dispatcher_send(
            hass, signal_params_update("other"), {DEVICE: {STATE_PARAM: True}}
        )
        await hass.async_block_till_done()

        assert entity.is_on is None
        state_writes(entity).assert_not_called()

    def test_a_report_for_another_device_instance_is_ignored(
        self, coordinator: MagicMock
    ) -> None:
        """One node can hold several devices; only ours matters."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        record_state_writes(entity)

        entity._handle_params_update({"Somewhere Else": {STATE_PARAM: True}})

        assert entity.is_on is None

    @pytest.mark.parametrize(("reported", "expected"), sorted(CLASS_MAP.items()))
    def test_device_class_follows_the_firmware(
        self, coordinator: MagicMock, reported: str, expected: str
    ) -> None:
        """The device decides what it is wired to, through the profile's map."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        record_state_writes(entity)

        _report(entity, **{CLASS_PARAM: reported})

        assert entity.device_class == BinarySensorDeviceClass(expected)

    def test_an_unmappable_device_class_is_kept_as_is(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A value outside the map must not clear a working device_class."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        record_state_writes(entity)

        _report(entity, **{CLASS_PARAM: "nonsense"})

        assert entity.device_class == BinarySensorDeviceClass.DOOR
        assert "unknown device class" in caplog.text

    def test_extra_attributes_come_from_the_profile(
        self, coordinator: MagicMock
    ) -> None:
        """Parameters declared as extra attributes are exposed, not dropped."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        record_state_writes(entity)

        _report(entity, debounce_time=50, report_interval=1000)

        assert entity.extra_state_attributes == {
            ATTR_BINARY_SENSOR_TYPE: "door",
            "debounce_time": 50,
            "report_interval": 1000,
        }

    def test_sensor_type_tracks_the_device_class(self, coordinator: MagicMock) -> None:
        """The legacy attribute keeps reporting whatever the device class is."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        record_state_writes(entity)

        _report(entity, **{CLASS_PARAM: "touch"})

        assert entity.extra_state_attributes[ATTR_BINARY_SENSOR_TYPE] == "occupancy"


class TestBinarySensorPlatformSetup:
    """Wiring the platform to binding discovery."""

    async def test_creates_an_entity_for_a_matching_binding(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A discovered binding on this platform becomes an entity."""
        entry = create_mock_config_entry()
        entry.runtime_data = coordinator
        entry.title = TEST_DEVICE_NAME
        added = AddedEntities()

        await async_setup_entry(hass, entry, added)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _binding(),
            {STATE_PARAM: True},
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].unique_id == f"{TEST_NODE_ID}_binary_sensor"
        assert added[0].is_on is True

    async def test_ignores_bindings_for_other_platforms(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Every platform hears every binding, so each must filter."""
        entry = create_mock_config_entry()
        entry.runtime_data = coordinator
        entry.title = TEST_DEVICE_NAME
        added = AddedEntities()

        await async_setup_entry(hass, entry, added)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _binding(platform="switch"),
            {},
        )
        await hass.async_block_till_done()

        assert added == []

    async def test_the_same_binding_twice_makes_one_entity(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Rediscovery must not produce a second entity for one unique_id."""
        entry = create_mock_config_entry()
        entry.runtime_data = coordinator
        entry.title = TEST_DEVICE_NAME
        added = AddedEntities()

        await async_setup_entry(hass, entry, added)

        for _ in range(2):
            async_dispatcher_send(
                hass, signal_binding_discovered(TEST_NODE_ID), _binding(), {}
            )
            await hass.async_block_till_done()

        assert len(added) == 1


class TestBinarySensorSubscription:
    """The entity only listens once it is added to Home Assistant."""

    async def test_listens_for_reports_after_being_added(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """State updates arrive over the shared raw parameter event."""
        entity = ESPWeaverBinarySensor(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        record_state_writes(entity)

        await entity.async_added_to_hass()

        async_dispatcher_send(
            hass, signal_params_update(TEST_NODE_ID), {DEVICE: {STATE_PARAM: True}}
        )
        await hass.async_block_till_done()

        assert entity.is_on is True
