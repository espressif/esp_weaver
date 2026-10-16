# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for value transforms and profile-to-node binding resolution."""

import copy
import logging
from typing import Any

import pytest

from custom_components.esp_weaver.profiles.registry import ProfileRegistry
from custom_components.esp_weaver.profiles.resolver import resolve_bindings
from custom_components.esp_weaver.profiles.schema import (
    AttributeSpec,
    DeviceProfile,
    ProfileError,
    validate_profile,
)
from custom_components.esp_weaver.profiles.transform import (
    clamp_to_bounds,
    device_to_ha,
    ha_to_device,
)
from tests.conftest import shipped_registry


def _spec(**kwargs: Any) -> AttributeSpec:
    """Build an attribute spec with sensible defaults."""
    kwargs.setdefault("name", "state")
    kwargs.setdefault("param", "Power")
    return AttributeSpec(**kwargs)


def _param(name: str, **kwargs: Any) -> dict[str, Any]:
    """Build a node-reported parameter definition."""
    return {"name": name, **kwargs}


def _node_config(*devices: dict[str, Any]) -> dict[str, Any]:
    """Wrap devices in a node config document."""
    return {"node_id": "node1", "devices": list(devices)}


class TestScale:
    """Linear scaling between device and HA ranges."""

    def test_device_to_ha_maps_range(self) -> None:
        """A 0-5 device value becomes a 0-100 HA value."""
        spec = _spec(
            param="Speed",
            data_type="int",
            bounds={"min": 0, "max": 5},
            scale={"to": [0, 100]},
        )
        assert device_to_ha(spec, 0) == 0
        assert device_to_ha(spec, 5) == 100
        assert device_to_ha(spec, 3) == 60

    def test_ha_to_device_reverses(self) -> None:
        """Writing an HA value converts back to the device range."""
        spec = _spec(
            param="Speed",
            data_type="int",
            bounds={"min": 0, "max": 5},
            scale={"to": [0, 100]},
        )
        assert ha_to_device(spec, 0) == 0
        assert ha_to_device(spec, 100) == 5
        assert ha_to_device(spec, 60) == 3

    def test_round_trips_on_exact_steps(self) -> None:
        """Every representable device value survives a round trip."""
        spec = _spec(
            param="Speed",
            data_type="int",
            bounds={"min": 0, "max": 5},
            scale={"to": [0, 100]},
        )
        for device_value in range(6):
            assert ha_to_device(spec, device_to_ha(spec, device_value)) == device_value

    def test_out_of_range_is_clamped(self) -> None:
        """Values beyond the target range are clamped, not extrapolated."""
        spec = _spec(
            param="Speed",
            data_type="int",
            bounds={"min": 0, "max": 5},
            scale={"to": [0, 100]},
        )
        assert device_to_ha(spec, 99) == 100
        assert ha_to_device(spec, 999) == 5

    def test_scale_without_bounds_is_ignored(self) -> None:
        """Scaling needs a source range; without one the value passes through."""
        spec = _spec(param="Speed", data_type="int", scale={"to": [0, 100]})
        assert device_to_ha(spec, 3) == 3

    def test_scale_with_only_one_end_is_ignored(self) -> None:
        """Half a range is not something to scale against, or to guess at."""
        spec = _spec(
            param="Speed",
            data_type="int",
            bounds={"max": 5, "step": 1},
            scale={"to": [0, 100]},
        )
        assert device_to_ha(spec, 3) == 3

    def test_zero_width_source_range(self) -> None:
        """A degenerate device range does not divide by zero."""
        spec = _spec(
            param="Speed",
            data_type="int",
            bounds={"min": 5, "max": 5},
            scale={"to": [0, 100]},
        )
        assert device_to_ha(spec, 5) == 0


class TestMap:
    """Value maps translate device encodings to HA vocabulary."""

    def test_translates_both_directions(self) -> None:
        """A map is applied on read and reversed on write."""
        spec = _spec(
            param="Mode",
            data_type="string",
            value_map={"0": "off", "1": "on"},
        )
        assert device_to_ha(spec, "0") == "off"
        assert ha_to_device(spec, "on") == "1"

    def test_unmapped_value_passes_through(self) -> None:
        """An unexpected device value is not swallowed."""
        spec = _spec(param="Mode", data_type="string", value_map={"0": "off"})
        assert device_to_ha(spec, "7") == "7"

    def test_ambiguous_reverse_uses_first(self) -> None:
        """Two device values mapping to one HA value write back the first."""
        spec = _spec(
            param="Mode",
            data_type="string",
            value_map={"0": "off", "9": "off"},
        )
        assert ha_to_device(spec, "off") == "0"

    def test_map_wins_over_scale(self) -> None:
        """A declared map short-circuits numeric scaling."""
        spec = _spec(
            param="Mode",
            data_type="int",
            value_map={1: 10},
            bounds={"min": 0, "max": 5},
            scale={"to": [0, 100]},
        )
        assert device_to_ha(spec, 1) == 10


class TestInvert:
    """Boolean inversion round-trips."""

    def test_invert_on_read_and_write(self) -> None:
        """An inverted bool flips in both directions."""
        spec = _spec(param="Closed", data_type="bool", invert=True)
        assert device_to_ha(spec, True) is False
        assert ha_to_device(spec, False) is True

    def test_invert_ignores_non_bool(self) -> None:
        """Inversion only applies to booleans."""
        spec = _spec(param="Level", data_type="int", invert=True)
        assert device_to_ha(spec, 5) == 5


class TestDeclaredOptions:
    """Reads are checked against the options a profile declares."""

    def test_value_outside_the_options_passes_through(self, caplog: Any) -> None:
        """An unexpected member is noted but still shown."""
        spec = _spec(param="Mode", data_type="string", options=("Normal", "Quick"))
        with caplog.at_level(logging.DEBUG):
            assert device_to_ha(spec, "Heavy") == "Heavy"
        assert "not in its declared options" in caplog.text

    def test_empty_value_reads_as_unknown(self, caplog: Any) -> None:
        """Nothing reported reads as unknown rather than as an empty state.

        A status parameter is empty until its first report, which for some
        nodes is every poll they ever answer.
        """
        spec = _spec(param="Status", data_type="string", options=("success", "failed"))
        with caplog.at_level(logging.DEBUG):
            assert device_to_ha(spec, "") is None
        assert "not in its declared options" not in caplog.text

    def test_empty_value_is_kept_where_nothing_is_declared(self) -> None:
        """Without options an empty string is a value like any other."""
        spec = _spec(param="Info", data_type="string")
        assert device_to_ha(spec, "") == ""


class TestCoercion:
    """Values are forced into the declared data type on write."""

    @pytest.mark.parametrize(
        ("data_type", "value", "expected"),
        [
            ("int", 3.7, 4),
            ("float", 3, 3.0),
            ("bool", 1, True),
            ("string", 12, "12"),
        ],
    )
    def test_coerces_to_data_type(
        self, data_type: str, value: Any, expected: Any
    ) -> None:
        """Each declared type produces the matching Python type."""
        spec = _spec(data_type=data_type)
        assert ha_to_device(spec, value) == expected

    def test_uncoercible_value_is_left_alone(self, caplog: Any) -> None:
        """A value that cannot be converted warns instead of raising."""
        spec = _spec(data_type="int")
        assert ha_to_device(spec, "abc") == "abc"
        assert "Cannot coerce" in caplog.text

    def test_none_is_preserved(self) -> None:
        """None means unknown and is never coerced."""
        spec = _spec(data_type="int")
        assert device_to_ha(spec, None) is None
        assert ha_to_device(spec, None) is None


class TestClamp:
    """Writes are clamped into the device's declared bounds."""

    def test_clamps_high_and_low(self) -> None:
        """Values outside the bounds are pulled to the nearest edge."""
        spec = _spec(param="Level", data_type="int", bounds={"min": 10, "max": 20})
        assert clamp_to_bounds(spec, 99) == 20
        assert clamp_to_bounds(spec, 1) == 10

    def test_in_range_value_untouched(self) -> None:
        """A value inside the bounds is returned as-is."""
        spec = _spec(param="Level", data_type="int", bounds={"min": 10, "max": 20})
        assert clamp_to_bounds(spec, 15) == 15

    def test_no_bounds_is_a_noop(self) -> None:
        """Without bounds nothing is clamped."""
        spec = _spec(param="Level", data_type="int")
        assert clamp_to_bounds(spec, 999) == 999

    def test_one_end_still_holds_that_end(self) -> None:
        """Half a limit is still a limit, and the device is the one that set it."""
        spec = _spec(param="Level", data_type="int", bounds={"max": 20})
        assert clamp_to_bounds(spec, 99) == 20
        assert clamp_to_bounds(spec, -99) == -99


def _registry_with(doc: dict[str, Any], source: str = "test.yaml") -> ProfileRegistry:
    """Build a one-file registry from a raw profile document."""
    validated = validate_profile(doc, source)
    registry = ProfileRegistry()
    registry.add_profiles(
        source,
        [DeviceProfile.from_dict(d, source) for d in validated.get("devices", [])],
        [DeviceProfile.from_dict(s, source) for s in validated.get("services", [])],
    )
    return registry


SWITCH_PROFILE: dict[str, Any] = {
    "version": 1,
    "model": "T1",
    "vendor": "acme",
    "product": "thing",
    "devices": [
        {
            "type": "esp.device.switch",
            "entities": [
                {
                    "platform": "switch",
                    "name": "Switch",
                    "key": "switch",
                    "attributes": {
                        "state": {
                            "param": "Power",
                            "data_type": "bool",
                            "properties": ["read", "write"],
                        }
                    },
                }
            ],
        }
    ],
}


def _switch_profile_with(**attribute: Any) -> dict[str, Any]:
    """Return SWITCH_PROFILE with its one attribute declared as given.

    Only ``param`` is kept; every other field is what the caller says, so a
    test can leave a field unsaid.
    """
    doc = copy.deepcopy(SWITCH_PROFILE)
    doc["devices"][0]["entities"][0]["attributes"]["state"] = {
        "param": "Power",
        **attribute,
    }
    return doc


class TestResolveBindings:
    """Profiles are matched against a node's reported config."""

    def test_binds_declared_parameter(self) -> None:
        """A device reporting the declared parameter yields a binding."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [
                    _param("Power", data_type="bool", properties=["read", "write"])
                ],
            }
        )

        bindings = resolve_bindings("node1", config, {}, registry)

        assert len(bindings) == 1
        binding = bindings[0]
        assert binding.device_name == "Light"
        assert binding.platform == "switch"
        assert binding.key == "switch"
        assert binding.primary is not None
        assert binding.primary.param == "Power"

    def test_missing_parameter_skips_entity(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An entity is dropped when the device lacks a declared parameter.

        And says which parameter, since names are matched exactly: a device that
        spells one differently is indistinguishable from one that lacks it.
        """
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [_param("Brightness", data_type="int")],
            }
        )

        with caplog.at_level(logging.INFO):
            assert resolve_bindings("node1", config, {}, registry) == []

        assert "'Power'" in caplog.text
        assert "matched exactly" in caplog.text

    def test_a_type_no_profile_declares_is_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A device nothing matched leaves no other trace that it was there.

        The entry sets up and the device appears with no entities at all, which
        is what an unreachable device looks like as well.
        """
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.doorbell",
                "params": [_param("Power", data_type="bool")],
            }
        )

        assert resolve_bindings("node1", config, {}, registry) == []
        assert "no profile declares device type 'esp.device.doorbell'" in caplog.text

    def test_a_type_that_is_nearly_right_says_what_it_nearly_is(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Off by a capital letter looks the same as unheard of, without this."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.Switch",
                "params": [_param("Power", data_type="bool")],
            }
        )

        assert resolve_bindings("node1", config, {}, registry) == []
        assert "Did you mean 'esp.device.switch'?" in caplog.text

    def _optional_profile(self) -> dict[str, Any]:
        """A light-shaped profile whose brightness is an optional capability."""
        return {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.light",
                    "entities": [
                        {
                            "platform": "light",
                            "name": "Light",
                            "key": "light",
                            "attributes": {
                                "state": {"param": "Power", "data_type": "bool"},
                                "brightness": {
                                    "param": "Brightness",
                                    "data_type": "int",
                                    "optional": True,
                                },
                            },
                        }
                    ],
                }
            ],
        }

    def test_a_missing_optional_attribute_keeps_the_entity(self) -> None:
        """A device without the optional capability still gets its entity."""
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.light",
                "params": [_param("Power", data_type="bool")],
            }
        )

        bindings = resolve_bindings(
            "node1", config, {}, _registry_with(self._optional_profile())
        )

        assert len(bindings) == 1
        assert set(bindings[0].attributes) == {"state"}

    def test_a_present_optional_attribute_is_bound(self) -> None:
        """A device that has the capability gets it wired up."""
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.light",
                "params": [
                    _param("Power", data_type="bool"),
                    _param("Brightness", data_type="int"),
                ],
            }
        )

        bindings = resolve_bindings(
            "node1", config, {}, _registry_with(self._optional_profile())
        )

        assert set(bindings[0].attributes) == {"state", "brightness"}

    def test_a_missing_required_attribute_still_skips_the_entity(self) -> None:
        """Optional is opt-in: everything else remains mandatory."""
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.light",
                "params": [_param("Brightness", data_type="int")],
            }
        )

        assert (
            resolve_bindings(
                "node1", config, {}, _registry_with(self._optional_profile())
            )
            == []
        )

    def test_unmapped_entity_is_skipped(self) -> None:
        """A parameter documented as having no HA equivalent stays unbound."""
        doc = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.switch",
                    "entities": [
                        {
                            "platform": "none",
                            "name": "Group ID",
                            "key": "group_id",
                            "attributes": {"value": {"param": "Power"}},
                        }
                    ],
                }
            ],
        }
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [_param("Power", data_type="bool")],
            }
        )

        assert resolve_bindings("node1", config, {}, _registry_with(doc)) == []

    def test_unknown_device_type_is_skipped(self) -> None:
        """A device type with no profile produces nothing."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {"name": "X", "type": "esp.device.mystery", "params": [_param("Power")]}
        )

        assert resolve_bindings("node1", config, {}, registry) == []

    def test_param_matching_is_exact(self) -> None:
        """Case and spacing differences do not match."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {"name": "Light", "type": "esp.device.switch", "params": [_param("power")]}
        )

        assert resolve_bindings("node1", config, {}, registry) == []


SERVICE_PROFILE: dict[str, Any] = {
    "version": 1,
    "model": "T1",
    "vendor": "acme",
    "product": "thing",
    "services": [
        {
            "type": "esp.service.cycle_timer",
            "entities": [
                {
                    "platform": "sensor",
                    "name": "Cycle Start",
                    "key": "cycle_start",
                    "attributes": {"state": {"param": "starttime", "data_type": "int"}},
                }
            ],
        }
    ],
}


class TestKeysAreUniquePerNode:
    """Two devices of one type each get an entity of their own."""

    @staticmethod
    def _two_switches(*names: str) -> dict[str, Any]:
        return _node_config(
            *[
                {
                    "name": name,
                    "type": "esp.device.switch",
                    "params": [
                        _param("Power", data_type="bool", properties=["read", "write"])
                    ],
                }
                for name in names
            ]
        )

    def test_a_two_gang_switch_yields_two_entities(self) -> None:
        """Both gangs must survive; the key is the unique_id."""
        registry = _registry_with(SWITCH_PROFILE)

        bindings = resolve_bindings(
            "node1", self._two_switches("Living Room", "Balcony"), {}, registry
        )

        assert [b.key for b in bindings] == ["switch", "switch_balcony"]
        assert [b.device_name for b in bindings] == ["Living Room", "Balcony"]

    def test_the_first_device_keeps_the_bare_key(self) -> None:
        """Renaming an existing entity's key would orphan it in the registry."""
        registry = _registry_with(SWITCH_PROFILE)

        bindings = resolve_bindings(
            "node1", self._two_switches("Only One"), {}, registry
        )

        assert [b.key for b in bindings] == ["switch"]

    def test_the_narrowed_entity_is_named_after_its_device(self) -> None:
        """Two entities called Switch would be indistinguishable in the UI."""
        registry = _registry_with(SWITCH_PROFILE)

        bindings = resolve_bindings(
            "node1", self._two_switches("Living Room", "Balcony"), {}, registry
        )

        assert [b.name for b in bindings] == ["Switch", "Balcony"]

    def test_a_device_named_after_the_entity_does_not_repeat_itself(self) -> None:
        """The standalone garage door lock is the real case for this.

        Its device name slugifies to the very key it collides with, so appending
        it would produce garage_door_lock_garage_door_lock.
        """
        doc = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.garage-door",
                    "entities": [
                        {
                            "platform": "lock",
                            "name": "Garage Door Lock",
                            "key": "garage_door_lock",
                            "attributes": {
                                "state": {
                                    "param": "Lock State",
                                    "data_type": "int",
                                    "properties": ["read", "write"],
                                }
                            },
                        }
                    ],
                },
                {
                    "type": "esp.device.garage-door-lock",
                    "entities": [
                        {
                            "platform": "lock",
                            "name": "Garage Door Lock",
                            "key": "garage_door_lock",
                            "attributes": {
                                "state": {
                                    "param": "Lock State",
                                    "data_type": "int",
                                    "properties": ["read", "write"],
                                }
                            },
                        }
                    ],
                },
            ],
        }
        registry = _registry_with(doc)
        config = _node_config(
            {
                "name": "Garage Door",
                "type": "esp.device.garage-door",
                "params": [
                    _param("Lock State", data_type="int", properties=["read", "write"])
                ],
            },
            {
                "name": "Garage Door Lock",
                "type": "esp.device.garage-door-lock",
                "params": [
                    _param("Lock State", data_type="int", properties=["read", "write"])
                ],
            },
        )

        bindings = resolve_bindings("node1", config, {}, registry)

        assert [b.key for b in bindings] == ["garage_door_lock", "garage_door_lock_2"]

    def test_a_name_no_more_distinguishing_than_the_key_gets_a_number(self) -> None:
        """Two rows reading Garage Door Lock cannot be told apart in the UI."""
        registry = _registry_with(SWITCH_PROFILE)

        bindings = resolve_bindings(
            "node1", self._two_switches("Balcony", "Switch"), {}, registry
        )

        assert [b.name for b in bindings] == ["Switch", "Switch 2"]
        assert [b.key for b in bindings] == ["switch", "switch_2"]

    def test_three_of_a_kind_all_get_through(self) -> None:
        """The narrowed key is retried until it is free."""
        registry = _registry_with(SWITCH_PROFILE)

        bindings = resolve_bindings(
            "node1", self._two_switches("A", "B", "C"), {}, registry
        )

        assert len({b.key for b in bindings}) == 3

    def test_untouched_bindings_keep_their_identity(self) -> None:
        """Narrowing must not disturb anything else on the binding."""
        registry = _registry_with(SWITCH_PROFILE)

        bindings = resolve_bindings(
            "node1", self._two_switches("Living Room", "Balcony"), {}, registry
        )

        narrowed = bindings[1]
        assert narrowed.platform == "switch"
        assert narrowed.device_type == "esp.device.switch"
        assert narrowed.primary is not None
        assert narrowed.primary.param == "Power"
        assert narrowed.primary.device_name == "Balcony"


class TestResolveNameScopedProfiles:
    """Resolution honours a profile entry narrowed to one device name."""

    @staticmethod
    def _profile() -> dict[str, Any]:
        """A type with a general mapping plus one scoped to "Switch 2"."""
        narrowed = {
            **SWITCH_PROFILE["devices"][0],
            "name": "Switch 2",
            "entities": [
                {
                    **SWITCH_PROFILE["devices"][0]["entities"][0],
                    "name": "Second Switch",
                    "key": "switch_2",
                }
            ],
        }
        return {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [SWITCH_PROFILE["devices"][0], narrowed],
        }

    @staticmethod
    def _device(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "type": "esp.device.switch",
            "params": [_param("Power", data_type="bool", properties=["read", "write"])],
        }

    def test_named_device_uses_the_narrowed_entry(self) -> None:
        """The device the profile names gets its own key."""
        bindings = resolve_bindings(
            "node1",
            _node_config(self._device("Switch 2")),
            {},
            _registry_with(self._profile()),
        )

        assert [b.key for b in bindings] == ["switch_2"]

    def test_other_devices_of_that_type_are_unaffected(self) -> None:
        """Narrowing one name leaves the rest of the type on the general entry."""
        bindings = resolve_bindings(
            "node1",
            _node_config(self._device("Switch 1"), self._device("Switch 2")),
            {},
            _registry_with(self._profile()),
        )

        assert [(b.device_name, b.key) for b in bindings] == [
            ("Switch 1", "switch"),
            ("Switch 2", "switch_2"),
        ]


class TestResolveServices:
    """Services resolve exactly like devices, from their own type namespace."""

    @staticmethod
    def _config() -> dict[str, Any]:
        return {
            "node_id": "node1",
            "devices": [],
            "services": [
                {
                    "name": "Cycle Timer",
                    "type": "esp.service.cycle_timer",
                    "params": [_param("starttime", data_type="int")],
                }
            ],
        }

    def test_binds_a_reported_service(self) -> None:
        """A service the node reports yields entities like any device."""
        bindings = resolve_bindings(
            "node1", self._config(), {}, _registry_with(SERVICE_PROFILE)
        )

        assert len(bindings) == 1
        assert bindings[0].device_name == "Cycle Timer"
        assert bindings[0].key == "cycle_start"

    def test_a_service_type_is_not_a_device_type(self) -> None:
        """The two namespaces stay separate: a device profile cannot match it."""
        as_device = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": SERVICE_PROFILE["services"],
        }

        assert (
            resolve_bindings("node1", self._config(), {}, _registry_with(as_device))
            == []
        )

    def test_services_share_the_params_payload(self) -> None:
        """A service reads its values from the same report devices use."""
        params = {"Cycle Timer": {"starttime": 1700000000}}

        bindings = resolve_bindings(
            "node1", self._config(), params, _registry_with(SERVICE_PROFILE)
        )

        primary = bindings[0].primary
        assert primary is not None
        assert primary.to_ha(params["Cycle Timer"][primary.param]) == 1700000000

    def test_a_config_without_services_is_fine(self) -> None:
        """Nodes that report no services resolve their devices as before."""
        registry = _registry_with({**SWITCH_PROFILE, **SERVICE_PROFILE})
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [
                    _param("Power", data_type="bool", properties=["read", "write"])
                ],
            }
        )

        assert [b.key for b in resolve_bindings("node1", config, {}, registry)] == [
            "switch"
        ]

    def test_unimplemented_platform_skipped(self) -> None:
        """Entities on platforms without an implementation are dropped."""
        doc = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.cam",
                    "entities": [
                        {
                            "platform": "camera",
                            "name": "Camera",
                            "key": "camera",
                            "attributes": {"state": {"param": "Power"}},
                        }
                    ],
                }
            ],
        }
        registry = _registry_with(doc)
        config = _node_config(
            {"name": "C", "type": "esp.device.cam", "params": [_param("Power")]}
        )

        assert resolve_bindings("node1", config, {}, registry) == []

    def test_missing_extra_attribute_is_tolerated(self) -> None:
        """Extra attributes are optional and do not block the entity."""
        doc = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.switch",
                    "entities": [
                        {
                            "platform": "switch",
                            "name": "Switch",
                            "key": "switch",
                            "attributes": {"state": {"param": "Power"}},
                            "extra_attributes": {
                                "debounce": {"param": "debounce_time"}
                            },
                        }
                    ],
                }
            ],
        }
        registry = _registry_with(doc)
        config = _node_config(
            {"name": "S", "type": "esp.device.switch", "params": [_param("Power")]}
        )

        binding = resolve_bindings("node1", config, {}, registry)[0]
        assert binding.extra_attributes == {}


def _dimmer_profile(**attribute: Any) -> dict[str, Any]:
    """Return a one-number profile whose attribute is declared as given."""
    return {
        "version": 1,
        "model": "T1",
        "vendor": "acme",
        "product": "thing",
        "devices": [
            {
                "type": "esp.device.dimmer",
                "entities": [
                    {
                        "platform": "number",
                        "name": "Level",
                        "key": "level",
                        "attributes": {"value": {"param": "Level", **attribute}},
                    }
                ],
            }
        ],
    }


def _dimmer_config(**param: Any) -> dict[str, Any]:
    """Return a node config whose dimmer reports ``Level`` as given."""
    return _node_config(
        {
            "name": "D",
            "type": "esp.device.dimmer",
            "params": [_param("Level", **param)],
        }
    )


class TestDeclaredMustMatchReported:
    """What a profile states about a parameter must be what the device reports.

    On any difference the entity is not created and the difference is logged;
    nothing is merged and neither side wins. A field the profile leaves out is
    taken from the device.
    """

    def _resolve(self, profile: dict[str, Any], config: dict[str, Any]) -> list[Any]:
        return resolve_bindings("node1", config, {}, _registry_with(profile))

    def test_matching_declarations_build_the_entity(self) -> None:
        """A profile that says exactly what the device says is fine."""
        bindings = self._resolve(
            _dimmer_profile(
                type="esp.param.brightness",
                data_type="int",
                properties=["read", "write"],
                bounds={"min": 0, "max": 100, "step": 1},
            ),
            _dimmer_config(
                type="esp.param.brightness",
                data_type="int",
                properties=["read", "write"],
                bounds={"min": 0, "max": 100, "step": 1},
            ),
        )

        assert len(bindings) == 1
        assert bindings[0].primary is not None
        assert bindings[0].primary.spec.bounds == {"min": 0, "max": 100, "step": 1}

    def test_different_bounds_skip_the_entity(self, caplog: Any) -> None:
        """Bounds the device reports otherwise are not merged in; the entity is dropped."""
        bindings = self._resolve(
            _dimmer_profile(data_type="int", bounds={"min": 0, "max": 100}),
            _dimmer_config(data_type="int", bounds={"min": 0, "max": 255}),
        )

        assert bindings == []
        assert "is not created" in caplog.text
        assert "bounds of 'Level'" in caplog.text
        assert "{'min': 0, 'max': 255}" in caplog.text

    def test_a_step_the_profile_leaves_out_is_a_difference(self) -> None:
        """Bounds are compared whole: a step on one side only is a difference."""
        bindings = self._resolve(
            _dimmer_profile(data_type="int", bounds={"min": 0, "max": 100}),
            _dimmer_config(data_type="int", bounds={"min": 0, "max": 100, "step": 5}),
        )

        assert bindings == []

    def test_bounds_are_compared_as_numbers(self) -> None:
        """0 and 0.0 are the same bound."""
        bindings = self._resolve(
            _dimmer_profile(data_type="float", bounds={"min": 0, "max": 1}),
            _dimmer_config(data_type="float", bounds={"min": 0.0, "max": 1.0}),
        )

        assert len(bindings) == 1

    def test_bounds_left_out_are_taken_from_the_device(self) -> None:
        """A profile that says nothing about bounds gets the device's."""
        bindings = self._resolve(
            _dimmer_profile(data_type="int"),
            _dimmer_config(data_type="int", bounds={"min": 0, "max": 255, "step": 5}),
        )

        assert bindings[0].primary is not None
        assert bindings[0].primary.spec.bounds == {"min": 0, "max": 255, "step": 5}
        assert bindings[0].primary.span == (0.0, 255.0)

    def test_different_access_skips_the_entity(self, caplog: Any) -> None:
        """A parameter the profile calls writable and the firmware read-only is a difference."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [_param("Power", data_type="bool", properties=["read"])],
            }
        )

        assert resolve_bindings("node1", config, {}, registry) == []
        assert "properties of 'Power'" in caplog.text
        assert "['read', 'write']" in caplog.text

    def test_access_left_out_is_taken_from_the_device(self) -> None:
        """Without properties in the profile, the device decides who may write."""
        registry = _registry_with(_switch_profile_with(data_type="bool"))

        writable = resolve_bindings(
            "node1",
            _node_config(
                {
                    "name": "Light",
                    "type": "esp.device.switch",
                    "params": [
                        _param("Power", data_type="bool", properties=["read", "write"])
                    ],
                }
            ),
            {},
            registry,
        )[0]
        read_only = resolve_bindings(
            "node1",
            _node_config(
                {
                    "name": "Light",
                    "type": "esp.device.switch",
                    "params": [_param("Power", data_type="bool", properties=["read"])],
                }
            ),
            {},
            registry,
        )[0]

        assert writable.writable is True
        assert read_only.writable is False

    def test_storage_flags_are_not_access(self, caplog: Any) -> None:
        """The SDK's persist and time_series say how a value is stored, not who may write it."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [
                    _param(
                        "Power",
                        data_type="bool",
                        properties=["read", "write", "time_series", "persist"],
                    )
                ],
            }
        )

        binding = resolve_bindings("node1", config, {}, registry)[0]

        assert binding.writable is True
        assert "is not created" not in caplog.text

    def test_different_data_type_skips_the_entity(self, caplog: Any) -> None:
        """A data type the device reports otherwise is not taken over."""
        bindings = self._resolve(
            _dimmer_profile(data_type="int"),
            _dimmer_config(data_type="float"),
        )

        assert bindings == []
        assert "data_type of 'Level'" in caplog.text

    def test_data_type_left_out_is_taken_from_the_device(self) -> None:
        """Without a data type in the profile, the device's is used for coercion."""
        bindings = self._resolve(_dimmer_profile(), _dimmer_config(data_type="float"))

        assert bindings[0].primary is not None
        assert bindings[0].primary.spec.data_type == "float"

    def test_different_parameter_type_skips_the_entity(self, caplog: Any) -> None:
        """The standard type a profile names must be the one the firmware reports."""
        bindings = self._resolve(
            _dimmer_profile(type="esp.param.brightness", data_type="int"),
            _dimmer_config(type="esp.param.speed", data_type="int"),
        )

        assert bindings == []
        assert "type of 'Level'" in caplog.text

    def test_every_difference_is_named_at_once(self, caplog: Any) -> None:
        """One message lists every field the two sides disagree on."""
        bindings = self._resolve(
            _dimmer_profile(
                data_type="int", properties=["read"], bounds={"min": 0, "max": 1}
            ),
            _dimmer_config(
                data_type="float",
                properties=["read", "write"],
                bounds={"min": 0, "max": 2},
            ),
        )

        assert bindings == []
        assert "data_type of 'Level'" in caplog.text
        assert "properties of 'Level'" in caplog.text
        assert "bounds of 'Level'" in caplog.text

    def test_a_parameter_the_device_lacks_cannot_conflict(self, caplog: Any) -> None:
        """Missing is missing; it is reported as such, not as a conflict."""
        bindings = self._resolve(
            _dimmer_profile(data_type="int", bounds={"min": 0, "max": 1}),
            _node_config({"name": "D", "type": "esp.device.dimmer", "params": []}),
        )

        assert bindings == []
        assert "does not report" in caplog.text
        assert "is not created, because the profile and the device" not in caplog.text

    def test_an_extra_attribute_is_held_to_the_same_rule(self, caplog: Any) -> None:
        """A read-only detail the profile describes wrongly drops the entity too."""
        doc = _dimmer_profile(data_type="int")
        doc["devices"][0]["entities"][0]["extra_attributes"] = {
            "raw": {"param": "Raw", "data_type": "int"}
        }
        config = _node_config(
            {
                "name": "D",
                "type": "esp.device.dimmer",
                "params": [
                    _param("Level", data_type="int"),
                    _param("Raw", data_type="string"),
                ],
            }
        )

        assert self._resolve(doc, config) == []
        assert "data_type of 'Raw'" in caplog.text


class TestDeviceClassOverride:
    """A reported device_class parameter overrides the declared one."""

    PROFILE: dict[str, Any] = {
        "version": 1,
        "model": "T1",
        "vendor": "acme",
        "product": "thing",
        "devices": [
            {
                "type": "esp.device.binary-sensor",
                "entities": [
                    {
                        "platform": "binary_sensor",
                        "name": "Binary Sensor",
                        "key": "binary_sensor",
                        "device_class": "door",
                        "attributes": {
                            "state": {"param": "State", "data_type": "bool"},
                            "device_class": {
                                "param": "device_class",
                                "data_type": "string",
                                "map": {
                                    "door": "door",
                                    "motion": "motion",
                                    "touch": "occupancy",
                                },
                            },
                        },
                    }
                ],
            }
        ],
    }

    @staticmethod
    def _config() -> dict[str, Any]:
        return _node_config(
            {
                "name": "Sensor",
                "type": "esp.device.binary-sensor",
                "params": [
                    _param("State", data_type="bool"),
                    _param("device_class", data_type="string"),
                ],
            }
        )

    def test_reported_value_overrides_default(self) -> None:
        """Reporting motion replaces the declared door class."""
        registry = _registry_with(self.PROFILE)
        params = {"Sensor": {"device_class": "motion"}}

        binding = resolve_bindings("node1", self._config(), params, registry)[0]
        assert binding.device_class == "motion"

    def test_map_is_applied_to_override(self) -> None:
        """The reported value goes through the map before being used."""
        registry = _registry_with(self.PROFILE)
        params = {"Sensor": {"device_class": "touch"}}

        binding = resolve_bindings("node1", self._config(), params, registry)[0]
        assert binding.device_class == "occupancy"

    def test_default_used_when_not_reported(self) -> None:
        """Without a reported value the declared class stands."""
        registry = _registry_with(self.PROFILE)

        binding = resolve_bindings("node1", self._config(), {}, registry)[0]
        assert binding.device_class == "door"


class TestOptionsFallback:
    """Where an entity's option list comes from.

    The profile's ``options``, which must be the firmware's ``valid_strs`` where
    it reports them; the firmware's alone where the profile declares none; the
    ``map``'s Home Assistant values where neither says.
    """

    @staticmethod
    def _profile(**attr: Any) -> dict[str, Any]:
        return {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.picker",
                    "entities": [
                        {
                            "platform": "sensor",
                            "name": "Mode",
                            "key": "mode",
                            "attributes": {
                                "state": {
                                    "param": "Mode",
                                    "data_type": "string",
                                    **attr,
                                }
                            },
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def _config(**param_extra: Any) -> dict[str, Any]:
        return _node_config(
            {
                "name": "P",
                "type": "esp.device.picker",
                "params": [_param("Mode", data_type="string", **param_extra)],
            }
        )

    def test_firmware_valid_strs_fill_in_for_undeclared_options(self) -> None:
        """Without options in the profile, the firmware's valid_strs are the list."""
        registry = _registry_with(self._profile())
        config = self._config(valid_strs=["x", "y"])

        binding = resolve_bindings("node1", config, {}, registry)[0]
        assert binding.options == ("x", "y")

    def test_declared_options_must_be_the_firmware_s(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Options the profile declares otherwise than the firmware drop the entity."""
        registry = _registry_with(self._profile(options=["a", "b"]))
        config = self._config(valid_strs=["x", "y"])

        assert resolve_bindings("node1", config, {}, registry) == []
        assert "options of 'Mode'" in caplog.text

    def test_a_spelling_the_profile_never_declared_is_a_difference(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Values are compared exactly, case included."""
        registry = _registry_with(self._profile(options=["cleaning", "docked"]))
        config = self._config(valid_strs=["Cleaning", "docked"])

        assert resolve_bindings("node1", config, {}, registry) == []
        assert "'Cleaning'" in caplog.text

    def test_a_narrower_device_is_a_difference(self) -> None:
        """A firmware listing fewer values than the profile declares is a difference."""
        registry = _registry_with(self._profile(options=["cleaning", "docked", "idle"]))
        config = self._config(valid_strs=["cleaning", "docked"])

        assert resolve_bindings("node1", config, {}, registry) == []

    def test_order_is_not_held_against_either_side(self) -> None:
        """The same values in another order are the same list; the profile's order shows."""
        registry = _registry_with(self._profile(options=["docked", "cleaning"]))
        config = self._config(valid_strs=["cleaning", "docked"])

        binding = resolve_bindings("node1", config, {}, registry)[0]
        assert binding.options == ("docked", "cleaning")

    def test_profile_options_stand_without_valid_strs(self) -> None:
        """Without valid_strs the declared options are used."""
        registry = _registry_with(self._profile(options=["a", "b"]))

        binding = resolve_bindings("node1", self._config(), {}, registry)[0]
        assert binding.options == ("a", "b")

    def test_map_values_are_last_resort(self) -> None:
        """With neither, the map's HA-side values become the options."""
        registry = _registry_with(self._profile(map={"0": "off", "1": "on"}))

        binding = resolve_bindings("node1", self._config(), {}, registry)[0]
        assert binding.options == ("off", "on")

    def test_no_options_at_all(self) -> None:
        """A plain parameter has no option list."""
        registry = _registry_with(self._profile())

        binding = resolve_bindings("node1", self._config(), {}, registry)[0]
        assert binding.options is None


class TestCommandAndReadIndex:
    """Bindings drive both the write path and the read path."""

    def test_command_payload_shape(self) -> None:
        """A write produces {device_name: {param: value}}."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [
                    _param("Power", data_type="bool", properties=["read", "write"])
                ],
            }
        )

        binding = resolve_bindings("node1", config, {}, registry)[0]
        assert binding.command(True) == {"Light": {"Power": True}}

    def test_read_only_entity_produces_no_command(self) -> None:
        """A read-only parameter refuses to build a write payload."""
        registry = _registry_with(_switch_profile_with(properties=["read"]))
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [_param("Power", data_type="bool", properties=["read"])],
            }
        )

        binding = resolve_bindings("node1", config, {}, registry)[0]
        assert binding.command(True) == {}

    def test_binding_carries_device_and_param(self) -> None:
        """The read path finds a value by the binding's device name and param."""
        registry = _registry_with(SWITCH_PROFILE)
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [_param("Power", data_type="bool")],
            }
        )

        binding = resolve_bindings("node1", config, {}, registry)[0]
        primary = binding.primary
        assert primary is not None
        assert (primary.device_name, primary.spec.param) == ("Light", "Power")

    def test_one_param_can_feed_several_entities(self) -> None:
        """A parameter used twice binds independently in each entity."""
        doc = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.dual",
                    "entities": [
                        {
                            "platform": "binary_sensor",
                            "name": "Sensor",
                            "key": "binary_sensor",
                            "attributes": {"state": {"param": "State"}},
                            "extra_attributes": {
                                "debounce": {"param": "debounce_time"}
                            },
                        },
                        {
                            "platform": "number",
                            "name": "Debounce",
                            "key": "debounce_time",
                            "attributes": {
                                "value": {
                                    "param": "debounce_time",
                                    "properties": ["read", "write"],
                                }
                            },
                        },
                    ],
                }
            ],
        }
        registry = _registry_with(doc)
        config = _node_config(
            {
                "name": "D",
                "type": "esp.device.dual",
                "params": [_param("State"), _param("debounce_time")],
            }
        )

        bindings = resolve_bindings("node1", config, {}, registry)
        readers = [
            (entity.key, param.spec.param)
            for entity in bindings
            for param in (
                *entity.attributes.values(),
                *entity.extra_attributes.values(),
            )
            if (param.device_name, param.spec.param) == ("D", "debounce_time")
        ]
        assert sorted(readers) == [
            ("binary_sensor", "debounce_time"),
            ("debounce_time", "debounce_time"),
        ]

    def _switch_binding(self) -> Any:
        """Resolve the shared switch profile against a one-parameter node."""
        config = _node_config(
            {
                "name": "Light",
                "type": "esp.device.switch",
                "params": [
                    _param("Power", data_type="bool", properties=["read", "write"])
                ],
            }
        )
        return resolve_bindings("node1", config, {}, _registry_with(SWITCH_PROFILE))[0]

    def test_commands_merge_into_one_payload(self) -> None:
        """Several attributes of one device become a single write."""
        binding = self._switch_binding()

        assert binding.commands({"state": True}) == {"Light": {"Power": True}}

    def test_commands_drop_attributes_the_entity_lacks(self) -> None:
        """Writing a capability the device never declared is a no-op."""
        binding = self._switch_binding()

        assert binding.commands({"brightness": 100}) == {}


class TestShippedProfilesResolve:
    """The real profiles bind against a realistic node config."""

    def test_binary_sensor_device_resolves(self) -> None:
        """esp.device.binary-sensor yields its implemented entities."""
        registry = shipped_registry()
        config = _node_config(
            {
                "name": "Binary Sensor",
                "type": "esp.device.binary-sensor",
                "params": [
                    _param("State", data_type="bool", properties=["read"]),
                    _param(
                        "device_class", data_type="string", properties=["read", "write"]
                    ),
                    _param(
                        "debounce_time",
                        data_type="int",
                        properties=["read", "write"],
                        bounds={"min": 10, "max": 1000, "step": 10},
                    ),
                    _param(
                        "report_interval",
                        data_type="int",
                        properties=["read", "write"],
                        bounds={"min": 100, "max": 10000, "step": 100},
                    ),
                ],
            }
        )
        params = {"Binary Sensor": {"State": False, "device_class": "motion"}}

        bindings = resolve_bindings("node1", config, params, registry)
        platforms = {b.platform for b in bindings}

        assert "binary_sensor" in platforms
        sensor = next(b for b in bindings if b.platform == "binary_sensor")
        assert sensor.device_class == "motion"

    def test_speaker_transport_commands_use_the_firmware_spelling(self) -> None:
        """A map is always firmware value -> HA value, transport commands included."""
        registry = shipped_registry()
        config = _node_config(
            {
                "name": "Speaker",
                "type": "esp.device.speaker",
                "params": [
                    _param("Power", data_type="bool", properties=["read", "write"]),
                    _param(
                        "Media Control",
                        data_type="string",
                        properties=["read", "write"],
                        valid_strs=["Play", "Pause", "Stop", "Next", "Previous"],
                    ),
                ],
            }
        )

        binding = resolve_bindings("node1", config, {}, registry)[0]
        control = binding.attributes["media_control"]

        assert control.ha_options == ("play", "pause", "stop", "next", "previous")
        assert control.to_device("play") == "Play"

    def test_device_with_no_matching_params_yields_nothing(self) -> None:
        """A node reporting unrelated parameters produces no entities."""
        registry = shipped_registry()
        config = _node_config(
            {
                "name": "Sensor",
                "type": "esp.device.sensor",
                "params": [_param("Totally Unrelated", data_type="int")],
            }
        )

        assert resolve_bindings("node1", config, {}, registry) == []

    def test_malformed_device_entry_is_skipped(self) -> None:
        """A non-dict entry in devices does not crash resolution."""
        registry = shipped_registry()
        config: dict[str, Any] = {"devices": ["not-a-device", None]}

        assert resolve_bindings("node1", config, {}, registry) == []

    def test_empty_config_is_safe(self) -> None:
        """A config with no devices resolves to nothing."""
        registry = shipped_registry()
        assert resolve_bindings("node1", {}, None, registry) == []


def _threshold_profile(**overrides: Any) -> dict[str, Any]:
    """A sensor bounded by two numbers, as the environment sensor profile is."""

    def number(name: str, key: str, param: str, end: str) -> dict[str, Any]:
        return {
            "platform": "number",
            "name": name,
            "key": key,
            "threshold": {"of": "temperature", "end": end},
            "attributes": {
                "value": {
                    "param": param,
                    "data_type": "float",
                    "properties": ["read", "write"],
                }
            },
        }

    entities = [
        {
            "platform": "sensor",
            "name": "Temperature",
            "key": "temperature",
            "attributes": {"state": {"param": "Temperature", "data_type": "float"}},
        },
        number("Min", "temperature_min_threshold", "temp_min", "min"),
        number("Max", "temperature_max_threshold", "temp_max", "max"),
    ]
    device = {"type": "esp.device.sensor", "entities": entities, **overrides}
    return {
        "version": 1,
        "model": "T1",
        "vendor": "acme",
        "product": "thing",
        "devices": [device],
    }


def _sensor_node(*params: str, name: str = "Sensor") -> dict[str, Any]:
    return {
        "name": name,
        "type": "esp.device.sensor",
        "params": [_param(p, data_type="float") for p in params],
    }


class TestThresholdDeclarations:
    """The profile says which numbers bound which sensor; nothing else does."""

    def test_the_sensor_learns_both_ends(self) -> None:
        """Both numbers reported: the sensor knows the key of each."""
        registry = _registry_with(_threshold_profile())

        bindings = resolve_bindings(
            "node1",
            _node_config(_sensor_node("Temperature", "temp_min", "temp_max")),
            {},
            registry,
        )

        by_key = {b.key: b for b in bindings}
        assert by_key["temperature"].thresholds == {
            "min": "temperature_min_threshold",
            "max": "temperature_max_threshold",
        }

    def test_each_number_knows_its_sensor_and_partner(self) -> None:
        """The cross-check on write needs the partner; the sensor is its owner."""
        registry = _registry_with(_threshold_profile())

        bindings = resolve_bindings(
            "node1",
            _node_config(_sensor_node("Temperature", "temp_min", "temp_max")),
            {},
            registry,
        )

        by_key = {b.key: b for b in bindings}
        minimum = by_key["temperature_min_threshold"].threshold
        assert minimum is not None
        assert (minimum.sensor_key, minimum.end) == ("temperature", "min")
        assert minimum.partner_key == "temperature_max_threshold"
        maximum = by_key["temperature_max_threshold"].threshold
        assert maximum is not None
        assert maximum.partner_key == "temperature_min_threshold"

    def test_an_unreported_end_is_simply_absent(self) -> None:
        """Firmware with only a maximum: no dangling minimum anywhere."""
        registry = _registry_with(_threshold_profile())

        bindings = resolve_bindings(
            "node1",
            _node_config(_sensor_node("Temperature", "temp_max")),
            {},
            registry,
        )

        by_key = {b.key: b for b in bindings}
        assert by_key["temperature"].thresholds == {"max": "temperature_max_threshold"}
        maximum = by_key["temperature_max_threshold"].threshold
        assert maximum is not None and maximum.partner_key is None

    def test_a_sensor_nobody_bounds_has_no_thresholds(self) -> None:
        """The common case costs nothing."""
        registry = _registry_with(SWITCH_PROFILE)
        bindings = resolve_bindings(
            "node1",
            _node_config(
                {
                    "name": "S",
                    "type": "esp.device.switch",
                    "params": [_param("Power", data_type="bool")],
                }
            ),
            {},
            registry,
        )
        assert bindings[0].thresholds == {}
        assert bindings[0].threshold is None

    def test_links_follow_a_renamed_second_device(self) -> None:
        """Two sensors of one type: the second's keys change, and so must its links."""
        registry = _registry_with(_threshold_profile())

        bindings = resolve_bindings(
            "node1",
            _node_config(
                _sensor_node("Temperature", "temp_min", "temp_max", name="Indoor"),
                _sensor_node("Temperature", "temp_min", "temp_max", name="Outdoor"),
            ),
            {},
            registry,
        )

        outdoor = {b.key: b for b in bindings if b.device_name == "Outdoor"}
        sensor_key = next(k for k, b in outdoor.items() if b.platform == "sensor")
        assert sensor_key != "temperature"
        sensor = outdoor[sensor_key]
        assert set(sensor.thresholds.values()) <= set(outdoor)
        for end, key in sensor.thresholds.items():
            role = outdoor[key].threshold
            assert role is not None
            assert role.sensor_key == sensor_key
            assert role.end == end
            assert role.partner_key in outdoor

    def test_a_threshold_must_name_a_sensor_of_the_block(self) -> None:
        """Bounding a sensor that does not exist would be silently dead."""
        doc = _threshold_profile()
        doc["devices"][0]["entities"][1]["threshold"]["of"] = "humidity"

        with pytest.raises(ProfileError, match="does not declare"):
            validate_profile(doc, "bad.yaml")

    def test_only_a_number_can_be_a_threshold(self) -> None:
        """A switch has no value to compare against."""
        doc = _threshold_profile()
        doc["devices"][0]["entities"][1]["platform"] = "switch"

        with pytest.raises(ProfileError, match="not a number"):
            validate_profile(doc, "bad.yaml")

    def test_one_end_cannot_be_declared_twice(self) -> None:
        """Two minimums leave the sensor with no way to choose."""
        doc = _threshold_profile()
        doc["devices"][0]["entities"][2]["threshold"]["end"] = "min"

        with pytest.raises(ProfileError, match="two min thresholds"):
            validate_profile(doc, "bad.yaml")

    def test_the_end_is_min_or_max(self) -> None:
        """Anything else is a typo."""
        doc = _threshold_profile()
        doc["devices"][0]["entities"][1]["threshold"]["end"] = "low"

        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")
