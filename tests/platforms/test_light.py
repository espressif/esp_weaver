# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven light platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest

from custom_components.esp_weaver.helpers.signals import signal_binding_discovered
from custom_components.esp_weaver.light import ESPWeaverLight, async_setup_entry
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

DEVICE = "Light"

# The parameter names the firmware uses, deliberately unlike the HA attribute
# names they are bound to.
POWER = "Power"
BRIGHTNESS = "Brightness"
HUE = "Hue"
SATURATION = "Saturation"
CCT = "CCT"
LIGHT_MODE = "Light Mode"
INTENSITY = "Intensity"


def _param(attribute: str, param: str, **spec: Any) -> ParamBinding:
    """Build one attribute binding with sensible read/write defaults."""
    spec.setdefault("properties", ("read", "write"))
    return ParamBinding(
        device_name=DEVICE,
        attribute=attribute,
        spec=AttributeSpec(name=attribute, param=param, **spec),
    )


def _state() -> ParamBinding:
    return _param("state", POWER, data_type="bool")


def _brightness() -> ParamBinding:
    """Brightness as the profile declares it: 0-100 on the wire, 0-255 in HA."""
    return _param(
        "brightness",
        BRIGHTNESS,
        data_type="int",
        bounds={"min": 0, "max": 100, "step": 1},
        scale={"to": [0, 255]},
    )


def _color() -> dict[str, ParamBinding]:
    return {
        "hue": _param("hue", HUE, data_type="int", bounds={"min": 0, "max": 360}),
        "saturation": _param(
            "saturation", SATURATION, data_type="int", bounds={"min": 0, "max": 100}
        ),
    }


def _color_temp() -> ParamBinding:
    return _param(
        "color_temp_kelvin",
        CCT,
        data_type="int",
        bounds={"min": 2700, "max": 6500, "step": 100},
    )


def _color_mode() -> ParamBinding:
    return _param(
        "color_mode",
        LIGHT_MODE,
        data_type="int",
        value_map={1: "hs", 2: "color_temp"},
    )


def _binding(attributes: dict[str, ParamBinding], **overrides: Any) -> EntityBinding:
    """Build a light binding out of the given attributes."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.light",
        "platform": "light",
        "name": "Ceiling Light",
        "key": "light",
        "attributes": attributes,
    }
    return EntityBinding(**{**defaults, **overrides})


def _full_binding(**overrides: Any) -> EntityBinding:
    """A light with every capability the shipped profile declares."""
    return _binding(
        {
            "state": _state(),
            "brightness": _brightness(),
            **_color(),
            "color_mode": _color_mode(),
            "color_temp_kelvin": _color_temp(),
        },
        **overrides,
    )


def _light(
    coordinator: MagicMock,
    binding: EntityBinding,
    report: dict[str, Any] | None = None,
    hass: HomeAssistant | None = None,
) -> ESPWeaverLight:
    """Build a light entity that is safe to drive without a real platform."""
    entity = ESPWeaverLight(coordinator, binding, TEST_DEVICE_NAME, report=report)
    if hass is not None:
        entity.hass = hass
    entity.entity_id = "light.test"
    record_state_writes(entity)
    return entity


def _report(entity: ESPWeaverLight, **params: Any) -> None:
    """Deliver a parameter report for this light's device instance."""
    entity._handle_params_update({DEVICE: params})


class TestLightCapabilities:
    """What the device reports decides which colour modes the light offers."""

    def test_power_only_is_an_onoff_light(self, coordinator: MagicMock) -> None:
        """A light with nothing but Power still becomes an entity."""
        entity = _light(coordinator, _binding({"state": _state()}))

        assert entity.supported_color_modes == {ColorMode.ONOFF}
        assert entity.color_mode == ColorMode.ONOFF
        assert entity.supported_features == LightEntityFeature(0)

    def test_brightness_makes_it_dimmable(self, coordinator: MagicMock) -> None:
        """Brightness without colour is a plain dimmable light."""
        entity = _light(
            coordinator, _binding({"state": _state(), "brightness": _brightness()})
        )

        assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
        assert entity.color_mode == ColorMode.BRIGHTNESS

    def test_hue_and_saturation_make_it_a_colour_light(
        self, coordinator: MagicMock
    ) -> None:
        """Both halves of hs_color must be present for colour support."""
        entity = _light(
            coordinator,
            _binding({"state": _state(), "brightness": _brightness(), **_color()}),
        )

        assert entity.supported_color_modes == {ColorMode.HS}

    def test_hue_without_saturation_is_not_colour(self, coordinator: MagicMock) -> None:
        """Half a colour pair cannot drive hs_color, so it falls back."""
        attributes = {"state": _state(), "brightness": _brightness()}
        attributes["hue"] = _color()["hue"]
        entity = _light(coordinator, _binding(attributes))

        assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
        assert entity.hs_color is None

    def test_full_light_supports_colour_and_temperature(
        self, coordinator: MagicMock
    ) -> None:
        """A light with both keeps both, which is what the profile declares."""
        entity = _light(coordinator, _full_binding())

        assert entity.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}

    def test_colour_temperature_range_comes_from_the_bounds(
        self, coordinator: MagicMock
    ) -> None:
        """HA needs the kelvin range the firmware actually accepts."""
        entity = _light(coordinator, _full_binding())

        assert entity.min_color_temp_kelvin == 2700
        assert entity.max_color_temp_kelvin == 6500

    def test_effect_support_needs_declared_options(
        self, coordinator: MagicMock
    ) -> None:
        """A profile can expose firmware effect modes as an effect list."""
        attributes = {
            "state": _state(),
            "effect": _param(
                "effect", "Scene", data_type="string", options=("Reading", "Movie")
            ),
        }
        entity = _light(coordinator, _binding(attributes))

        assert entity.supported_features == LightEntityFeature.EFFECT
        assert entity.effect_list == ["Reading", "Movie"]


class TestLightIdentity:
    """Identity and naming come from the profile."""

    def test_unique_id_comes_from_the_profile_key(self, coordinator: MagicMock) -> None:
        """Renaming a light in the profile must not orphan the entity."""
        entity = _light(coordinator, _full_binding())

        assert entity.unique_id == f"{TEST_NODE_ID}_light"
        assert entity.translation_key == "light"
        assert entity.name == "Ceiling Light"


class TestLightReads:
    """Turning device reports into Home Assistant state."""

    def test_seeds_itself_from_the_discovery_report(
        self, coordinator: MagicMock
    ) -> None:
        """A new light starts with what the node already published."""
        entity = _light(
            coordinator,
            _full_binding(),
            report={POWER: True, BRIGHTNESS: 100, HUE: 240, SATURATION: 50},
        )

        assert entity.is_on is True
        assert entity.brightness == 255
        assert entity.hs_color == (240.0, 50.0)

    def test_brightness_is_scaled_into_the_ha_range(
        self, coordinator: MagicMock
    ) -> None:
        """The profile's scale is the only brightness conversion there is."""
        entity = _light(coordinator, _full_binding(), report={POWER: True})

        _report(entity, **{BRIGHTNESS: 50})

        assert entity.brightness == 128

    def test_brightness_is_hidden_while_the_light_is_off(
        self, coordinator: MagicMock
    ) -> None:
        """Home Assistant expects no brightness from a light that is off."""
        entity = _light(
            coordinator, _full_binding(), report={POWER: False, BRIGHTNESS: 100}
        )

        assert entity.brightness is None

    def test_colour_mode_follows_the_mapped_parameter(
        self, coordinator: MagicMock
    ) -> None:
        """Light Mode 2 means the device is in colour temperature mode."""
        entity = _light(coordinator, _full_binding(), report={POWER: True})

        _report(entity, **{LIGHT_MODE: 2, CCT: 4000})

        assert entity.color_mode == ColorMode.COLOR_TEMP
        assert entity.color_temp_kelvin == 4000

    def test_unknown_colour_mode_falls_back(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unmapped mode is logged, and the light keeps working."""
        binding = _binding(
            {
                "state": _state(),
                **_color(),
                "color_temp_kelvin": _color_temp(),
                "color_mode": _param("color_mode", LIGHT_MODE, data_type="string"),
            }
        )
        entity = _light(coordinator, binding, report={POWER: True})

        _report(entity, **{LIGHT_MODE: "disco"})

        assert entity.color_mode == ColorMode.HS
        assert "unknown colour mode" in caplog.text

    def test_extra_attributes_are_exposed(self, coordinator: MagicMock) -> None:
        """Parameters with no HA equivalent stay visible as detail."""
        binding = _full_binding(
            extra_attributes={"intensity": _param("intensity", INTENSITY)}
        )
        entity = _light(coordinator, binding, report={POWER: True, INTENSITY: 7})

        assert entity.extra_state_attributes["intensity"] == 7

    def test_a_report_that_changes_nothing_writes_nothing(
        self, coordinator: MagicMock
    ) -> None:
        """Repeating known values must not wake Home Assistant up."""
        entity = _light(coordinator, _full_binding(), report={POWER: True})

        _report(entity, **{POWER: True})

        state_writes(entity).assert_not_called()

    def test_a_report_for_another_device_is_ignored(
        self, coordinator: MagicMock
    ) -> None:
        """The same parameter name on another device instance is not ours."""
        entity = _light(coordinator, _full_binding())

        entity._handle_params_update({"Other Light": {POWER: True}})

        # Still unknown rather than off: our own light has yet to report.
        assert entity.is_on is None


class TestLightWrites:
    """Turning service calls into device payloads."""

    async def test_turn_on_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The payload names the device instance the binding resolved."""
        entity = _light(coordinator, _full_binding(), hass=hass)

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )
        assert entity.is_on is True

    async def test_turn_off_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Turning off is a single parameter write."""
        entity = _light(coordinator, _full_binding(), report={POWER: True}, hass=hass)

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.is_on is False

    async def test_brightness_is_scaled_back_down(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """HA's 0-255 must reach the device as the 0-100 it declared."""
        entity = _light(coordinator, _full_binding(), hass=hass)

        await entity.async_turn_on(brightness=255)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, BRIGHTNESS: 100}}
        )

    async def test_power_and_colour_travel_together(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """One write, so the device never applies half the change."""
        entity = _light(coordinator, _full_binding(), hass=hass)

        await entity.async_turn_on(hs_color=(120.0, 80.0))

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID,
            {DEVICE: {POWER: True, HUE: 120, SATURATION: 80, LIGHT_MODE: 1}},
        )

    async def test_colour_temperature_selects_its_mode(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A light that can do both must be told which mode a write means."""
        entity = _light(coordinator, _full_binding(), hass=hass)

        await entity.async_turn_on(color_temp_kelvin=4000)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True, CCT: 4000, LIGHT_MODE: 2}}
        )

    async def test_values_are_clamped_to_the_reported_bounds(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The firmware's own limits are the last word on a write."""
        entity = _light(coordinator, _full_binding(), hass=hass)

        await entity.async_turn_on(color_temp_kelvin=9000)

        _, payload = coordinator.api.set_params.await_args.args
        assert payload[DEVICE][CCT] == 6500

    async def test_capabilities_the_device_lacks_are_dropped(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Asking an on/off light for colour writes power and nothing else."""
        entity = _light(coordinator, _binding({"state": _state()}), hass=hass)

        await entity.async_turn_on(brightness=200, hs_color=(120.0, 80.0))

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )

    async def test_a_read_only_light_refuses_the_write(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A light the firmware will not accept writes for raises instead."""
        binding = _binding(
            {"state": _param("state", POWER, data_type="bool", properties=("read",))}
        )
        entity = _light(coordinator, binding, hass=hass)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "write_not_supported"

        coordinator.api.set_params.assert_not_called()


class TestLightDiscovery:
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

    async def test_creates_a_light_for_a_light_binding(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A light binding for this node produces one seeded entity."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _full_binding(),
            {POWER: True, BRIGHTNESS: 100},
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].is_on is True
        assert added[0].brightness == 255

    async def test_ignores_bindings_for_other_platforms(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A switch binding is not a light."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _full_binding(platform="switch"),
            {},
        )
        await hass.async_block_till_done()

        assert added == []

    async def test_does_not_create_the_same_light_twice(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Rediscovery after a reconnect must not duplicate entities."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        for _ in range(2):
            async_dispatcher_send(
                hass, signal_binding_discovered(TEST_NODE_ID), _full_binding(), {}
            )
            await hass.async_block_till_done()

        assert len(added) == 1
