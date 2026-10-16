# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the read-only profile preview used by diagnostics."""

from typing import Any

from custom_components.esp_weaver.profiles.preview import (
    REASON_CONFLICT,
    REASON_MISSING_PARAMS,
    REASON_PLATFORM,
    REASON_UNMAPPED,
    build_preview,
    compare_unique_ids,
    describe_reported,
    setup_report,
)
from custom_components.esp_weaver.profiles.registry import ProfileRegistry
from custom_components.esp_weaver.profiles.resolver import resolve_bindings
from custom_components.esp_weaver.profiles.schema import (
    IMPLEMENTED_PLATFORMS,
    DeviceProfile,
    validate_profile,
)

NODE_ID = "node1"

# A real Home Assistant platform that esp_weaver has not built yet, used by the
# fixture below to exercise the "skipped for its platform" path. Implementing it
# will trip the guard in test_fixture_platform_is_still_unimplemented, which is
# the cue to pick another one rather than to change what these tests assert.
UNIMPLEMENTED_PLATFORM = "camera"

PROFILE: dict[str, Any] = {
    "version": 1,
    "model": "T1",
    "vendor": "acme",
    "product": "thing",
    "devices": [
        {
            "type": "esp.device.sensor",
            "entities": [
                {
                    "platform": "sensor",
                    "name": "Ambient Temperature",
                    "key": "ambient_temperature",
                    "unit": "°C",
                    "attributes": {
                        "state": {"param": "Ambient Temperature", "data_type": "float"}
                    },
                },
                {
                    "platform": "sensor",
                    "name": "CO2",
                    "key": "co2",
                    "attributes": {"state": {"param": "CO2", "data_type": "int"}},
                },
                {
                    "platform": UNIMPLEMENTED_PLATFORM,
                    "name": "Thermostat",
                    "key": "thermostat",
                    "attributes": {
                        "state": {"param": "Ambient Temperature", "data_type": "float"}
                    },
                },
            ],
        }
    ],
}


def _registry(doc: dict[str, Any] = PROFILE) -> ProfileRegistry:
    """Build a one-file registry from a raw profile document."""
    validated = validate_profile(doc, "test.yaml")
    registry = ProfileRegistry()
    registry.add_profiles(
        "test.yaml",
        [DeviceProfile.from_dict(d, "test.yaml") for d in validated["devices"]],
        [],
    )
    return registry


def _node_config(device_type: str, *params: str) -> dict[str, Any]:
    """Build a node config reporting one device with the given parameters."""
    return {
        "node_id": NODE_ID,
        "devices": [
            {
                "name": "Sensor",
                "type": device_type,
                "params": [{"name": name} for name in params],
            }
        ],
    }


class TestDescribeReported:
    """The summary of what the node itself said."""

    def test_lists_device_types_and_param_names(self) -> None:
        """Every device contributes its type and its parameter names."""
        config = _node_config("esp.device.sensor", "CO2", "Ambient Temperature")

        assert describe_reported(config) == [
            {
                "kind": "device",
                "name": "Sensor",
                "type": "esp.device.sensor",
                "params": ["CO2", "Ambient Temperature"],
            }
        ]

    def test_tolerates_a_missing_devices_section(self) -> None:
        """A config without devices summarises as nothing rather than raising."""
        assert describe_reported({}) == []

    def test_skips_malformed_entries(self) -> None:
        """Non-mapping devices and nameless params are ignored."""
        config = {"devices": ["junk", {"name": "S", "type": "t", "params": [{}, 1]}]}

        assert describe_reported(config) == [
            {"kind": "device", "name": "S", "type": "t", "params": []}
        ]

    def test_distinguishes_services_from_devices(self) -> None:
        """Services are summarised too, tagged so the two cannot be confused."""
        config = {
            "devices": [{"name": "Sensor", "type": "esp.device.sensor", "params": []}],
            "services": [{"name": "Schedule", "type": "esp.service.schedule"}],
        }

        assert [(e["kind"], e["name"]) for e in describe_reported(config)] == [
            ("device", "Sensor"),
            ("service", "Schedule"),
        ]


class TestPreviewMatching:
    """What the preview reports when profile and firmware agree or do not."""

    def test_exact_names_produce_bindings(self) -> None:
        """Matching device type and parameter names resolve to entities."""
        config = _node_config("esp.device.sensor", "Ambient Temperature", "CO2")

        preview = build_preview(_registry(), NODE_ID, config)

        assert [entity["key"] for entity in preview["entities"]] == [
            "ambient_temperature",
            "co2",
        ]
        assert preview["entities"][0]["unique_id"] == (f"{NODE_ID}_ambient_temperature")
        assert preview["entities"][0]["params"] == {"state": "Ambient Temperature"}

    def test_normalised_param_names_match_nothing(self) -> None:
        """Lowercased, underscored parameter names are a different string."""
        config = _node_config("esp.device.sensor", "ambient_temperature", "co2")

        preview = build_preview(_registry(), NODE_ID, config)

        assert preview["entities"] == []
        missing = {
            entry["key"]: entry
            for entry in preview["skipped"]["entities"]
            if entry["reason"] == REASON_MISSING_PARAMS
        }
        assert missing["ambient_temperature"]["missing"] == ["Ambient Temperature"]
        assert missing["co2"]["missing"] == ["CO2"]

    def test_optional_attribute_does_not_skip_the_entity(self) -> None:
        """An entity created despite a missing optional param is not also skipped."""
        doc: dict[str, Any] = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.lightbulb",
                    "entities": [
                        {
                            "platform": "light",
                            "name": "Lightbulb",
                            "key": "lightbulb",
                            "attributes": {
                                "state": {
                                    "param": "Power",
                                    "data_type": "bool",
                                    "properties": ["read", "write"],
                                },
                                "hue": {
                                    "param": "Hue",
                                    "data_type": "int",
                                    "optional": True,
                                },
                            },
                        }
                    ],
                }
            ],
        }
        config = _node_config("esp.device.lightbulb", "Power")

        preview = build_preview(_registry(doc), NODE_ID, config)

        assert [entity["key"] for entity in preview["entities"]] == ["lightbulb"]
        assert preview["skipped"]["entities"] == []

    def test_unknown_device_type_is_named(self) -> None:
        """A device type absent from the registry is reported, not swallowed."""
        config = _node_config(
            "esp.device.temperature-sensor", "Ambient Temperature", "CO2"
        )

        preview = build_preview(_registry(), NODE_ID, config)

        assert preview["entities"] == []
        assert preview["skipped"]["device_types"] == [
            {
                "kind": "device",
                "name": "Sensor",
                "type": "esp.device.temperature-sensor",
            }
        ]
        # Entity-level reasons need a profile, so there are none to report.
        assert preview["skipped"]["entities"] == []

    def test_fixture_platform_is_still_unimplemented(self) -> None:
        """Guard the assumption the two tests below are built on."""
        assert UNIMPLEMENTED_PLATFORM not in IMPLEMENTED_PLATFORMS

    def test_unimplemented_platform_is_reported_separately(self) -> None:
        """An entity is skipped for its platform, not for its params."""
        config = _node_config("esp.device.sensor", "Ambient Temperature", "CO2")

        preview = build_preview(_registry(), NODE_ID, config)

        assert preview["skipped"]["entities"] == [
            {
                "device_type": "esp.device.sensor",
                "name": "Thermostat",
                "key": "thermostat",
                "reason": REASON_PLATFORM,
                "platform": UNIMPLEMENTED_PLATFORM,
            }
        ]

    def test_unmapped_entity_has_its_own_reason(self) -> None:
        """A recorded decision must not read like a missing implementation."""
        doc: dict[str, Any] = {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": "esp.device.sensor",
                    "entities": [
                        {
                            "platform": "none",
                            "name": "Schedules",
                            "key": "schedules",
                            "attributes": {"value": {"param": "CO2"}},
                        }
                    ],
                }
            ],
        }
        config = _node_config("esp.device.sensor", "CO2")

        preview = build_preview(_registry(doc), NODE_ID, config)

        assert preview["skipped"]["entities"] == [
            {
                "device_type": "esp.device.sensor",
                "name": "Schedules",
                "key": "schedules",
                "reason": REASON_UNMAPPED,
            }
        ]

    def test_carries_the_registry_description(self) -> None:
        """The preview says which files were loaded."""
        preview = build_preview(
            _registry(), NODE_ID, _node_config("esp.device.sensor", "CO2")
        )

        assert preview["registry"]["sources"] == ["test.yaml"]
        assert preview["registry"]["device_types"] == ["esp.device.sensor"]


class TestComparison:
    """Diffing predicted unique_ids against the ones in the entity registry."""

    def test_splits_into_three_buckets(self) -> None:
        """Shared, profile-only and registry-only ids are separated."""
        config = _node_config("esp.device.sensor", "Ambient Temperature", "CO2")
        bindings = resolve_bindings(NODE_ID, config, None, _registry())
        existing = {
            f"{NODE_ID}_ambient_temperature",
            f"{NODE_ID}_humidity",
        }

        result = compare_unique_ids(bindings, existing)

        assert result["matched"] == [f"{NODE_ID}_ambient_temperature"]
        assert result["only_in_bindings"] == [f"{NODE_ID}_co2"]
        assert result["only_in_registry"] == [f"{NODE_ID}_humidity"]

    def test_absent_unless_asked_for(self) -> None:
        """Without a registry snapshot the preview omits the comparison."""
        preview = build_preview(
            _registry(), NODE_ID, _node_config("esp.device.sensor", "CO2")
        )

        assert "comparison" not in preview

    def test_included_when_asked_for(self) -> None:
        """Passing a snapshot adds the comparison section."""
        preview = build_preview(
            _registry(),
            NODE_ID,
            _node_config("esp.device.sensor", "CO2"),
            existing_unique_ids=set(),
        )

        assert preview["comparison"]["only_in_bindings"] == [f"{NODE_ID}_co2"]


class TestPreviewIsInformationOnly:
    """The preview must not leak device values into diagnostics."""

    def test_reports_names_but_no_values(self) -> None:
        """Parameter values are used for metadata only, never echoed back."""
        config = _node_config("esp.device.sensor", "Ambient Temperature")
        params = {"Sensor": {"Ambient Temperature": 23.5}}

        preview = build_preview(_registry(), NODE_ID, config, params)

        assert "23.5" not in repr(preview)


class TestSetupReport:
    """What the preview reduces to when someone is adding the device."""

    def test_a_missing_parameter_is_kept(self) -> None:
        """Firmware and profile disagreeing on a name is theirs to fix."""
        config = _node_config("esp.device.sensor", "ambient_temperature")

        report = setup_report(build_preview(_registry(), NODE_ID, config))

        assert [entry["key"] for entry in report["skipped"]] == [
            "ambient_temperature",
            "co2",
        ]
        assert report["skipped"][0]["reason"] == REASON_MISSING_PARAMS
        assert report["skipped"][0]["missing"] == ["Ambient Temperature"]

    def test_a_parameter_described_otherwise_is_kept(self) -> None:
        """Firmware and profile disagreeing on what a parameter is, likewise."""
        config = _node_config("esp.device.sensor", "Ambient Temperature", "CO2")
        config["devices"][0]["params"][0]["data_type"] = "string"

        report = setup_report(build_preview(_registry(), NODE_ID, config))

        assert [entry["key"] for entry in report["skipped"]] == ["ambient_temperature"]
        entry = report["skipped"][0]
        assert entry["reason"] == REASON_CONFLICT
        assert entry["conflicts"] == [
            {
                "attribute": "state",
                "param": "Ambient Temperature",
                "field": "data_type",
                "declared": "float",
                "reported": "string",
            }
        ]
        assert entry["details"] == [
            "data_type of 'Ambient Temperature': the profile declares 'float', "
            "the device reports 'string'"
        ]
        assert [entry["key"] for entry in report["created"]] == ["co2"]

    def test_an_unbuilt_platform_is_dropped(self) -> None:
        """The same for everyone, and not something a user can act on."""
        config = _node_config("esp.device.sensor", "Ambient Temperature", "CO2")
        preview = build_preview(_registry(), NODE_ID, config)

        report = setup_report(preview)

        assert any(
            entry["reason"] == REASON_PLATFORM
            for entry in preview["skipped"]["entities"]
        )
        assert report["skipped"] == []

    def test_a_type_no_profile_claimed_is_kept(self) -> None:
        """That device produces nothing at all, which is worth reporting."""
        config = _node_config("esp.device.aroma", "Power")

        report = setup_report(build_preview(_registry(), NODE_ID, config))

        assert [entry["type"] for entry in report["unknown_types"]] == [
            "esp.device.aroma"
        ]

    def test_the_created_entities_come_through(self) -> None:
        """The count of what worked is the headline the form leads with."""
        config = _node_config("esp.device.sensor", "Ambient Temperature", "CO2")

        report = setup_report(build_preview(_registry(), NODE_ID, config))

        assert [entry["key"] for entry in report["created"]] == [
            "ambient_temperature",
            "co2",
        ]

    def test_an_empty_preview_reports_nothing_rather_than_raising(self) -> None:
        """Every key is optional: a preview that failed still has to render."""
        assert setup_report({}) == {
            "created": [],
            "skipped": [],
            "unknown_types": [],
        }
