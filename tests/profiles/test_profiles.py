# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for profile schema validation, the shipped products and the catalog."""

from pathlib import Path
import subprocess
from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
import voluptuous as vol
import yaml

from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.profiles import loader as loader_module
from custom_components.esp_weaver.profiles.catalog import (
    async_get_catalog,
    async_refresh_catalog,
)
from custom_components.esp_weaver.profiles.loader import (
    BUILTIN_VENDOR,
    CUSTOM_PROFILE_DIR,
    PROFILE_DIR,
    SERVICE_LAYER_FILE,
    builtin_registry,
    load_service_layer,
    scan_builtin,
    scan_local,
)
from custom_components.esp_weaver.profiles.product import (
    SOURCE_BUILTIN,
    SOURCE_LOCAL,
    ProductCandidate,
)
from custom_components.esp_weaver.profiles.registry import ProfileRegistry
from custom_components.esp_weaver.profiles.schema import (
    IMPLEMENTED_PLATFORMS,
    MODEL_GENERIC,
    PLATFORM_UNMAPPED,
    DeviceProfile,
    EntitySpec,
    ProfileError,
    content_hash,
    parse_profile_document,
    profile_blocks,
    validate_profile,
    validate_service_layer,
)
from tests.conftest import shipped_registry


def _shipped_profiles() -> list[str]:
    """Return the profile files the repository ships, asked of git."""
    found = subprocess.run(
        ["git", "ls-files", "*.yaml", "**/*.yaml"],
        cwd=PROFILE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0:  # No git, or no checkout: take what is there.
        return sorted(
            str(p.relative_to(PROFILE_DIR)) for p in PROFILE_DIR.rglob("*.yaml")
        )
    return sorted(set(found.stdout.split()))


# Every profile the repository ships, as git sees it: the service layer and the
# products under espressif/.
ALL_PROFILE_FILES = _shipped_profiles()

# The device types Espressif ships a product for: the 19 standard RainMaker
# devices with defined entities and the 16 esp_weaver extensions, one product
# each. ``esp.device.other`` and ``esp.device.thread-br`` have no entities and
# are deliberately not products users can select. A node of several types
# needs a profile declaring exactly those, which no shipped product does.
STANDARD_TYPES = [
    "esp.device.switch",
    "esp.device.lightbulb",
    "esp.device.light",
    "esp.device.fan",
    "esp.device.temperature-sensor",
    "esp.device.outlet",
    "esp.device.plug",
    "esp.device.socket",
    "esp.device.lock",
    "esp.device.blinds-internal",
    "esp.device.blinds-external",
    "esp.device.garage-door",
    "esp.device.garage-door-lock",
    "esp.device.speaker",
    "esp.device.air-conditioner",
    "esp.device.thermostat",
    "esp.device.tv",
    "esp.device.washer",
    "esp.device.zigbee_gateway",
]
EXTENDED_TYPES = [
    "esp.device.sensor",
    "esp.device.binary-sensor",
    "esp.device.humidity-sensor",
    "esp.device.event",
    "esp.device.alarm-panel",
    "esp.device.siren",
    "esp.device.valve",
    "esp.device.humidifier",
    "esp.device.vacuum",
    "esp.device.lawn-mower",
    "esp.device.remote-control",
    "esp.device.water-heater-control",
    "esp.device.imu-gesture",
    "esp.device.interactive-input",
    "esp.device.low-power-sleep",
    "esp.device.battery-energy",
]
SHIPPED_TYPES = [*STANDARD_TYPES, *EXTENDED_TYPES]


def _product_for(device_type: str) -> str:
    """Return the id of the shipped product for a device type."""
    return (
        f"{BUILTIN_VENDOR}/{device_type.removeprefix('esp.device.').replace('-', '_')}"
    )


SHIPPED_PRODUCTS = {_product_for(t) for t in SHIPPED_TYPES}


def _minimal_profile(
    device_type: str,
    entity_name: str = "State",
    device_name: str | None = None,
) -> dict[str, Any]:
    """Build the smallest valid profile document."""
    device: dict[str, Any] = {"type": device_type}
    if device_name is not None:
        device["name"] = device_name
    return {
        "version": 1,
        "model": "T1",
        "vendor": "acme",
        "product": "thing",
        "devices": [
            {
                **device,
                "entities": [
                    {
                        "platform": "switch",
                        "name": entity_name,
                        "key": "state",
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


def _implemented_entities(
    registry: ProfileRegistry,
    device_type: str,
    device_name: str | None = None,
) -> list[EntitySpec]:
    """Mirror the filter the resolver applies before it builds a binding."""
    profile = registry.get_device(device_type, device_name)
    if profile is None:
        return []
    return [entity for entity in profile.entities if entity.implemented]


def _device(
    device_type: str, source: str, entity_name: str, name: str | None = None
) -> DeviceProfile:
    """Return one validated device block as a profile."""
    doc = validate_profile(_minimal_profile(device_type, entity_name, name), source)
    return DeviceProfile.from_dict(doc["devices"][0], source)


class TestSchema:
    """Schema validation of shipped and synthetic profiles."""

    @pytest.mark.parametrize("filename", ALL_PROFILE_FILES)
    def test_shipped_profiles_validate(self, filename: str) -> None:
        """Every profile on disk parses and validates, offered or not."""
        path = PROFILE_DIR / filename
        assert path.is_file(), f"{filename} is missing from {PROFILE_DIR}"
        validate = (
            validate_service_layer
            if filename == SERVICE_LAYER_FILE
            else validate_profile
        )
        validate(yaml.safe_load(path.read_text(encoding="utf-8")), filename)

    def test_the_product_fields_survive_validation(self) -> None:
        """Vendor, product and label are what the list is built from."""
        doc = {
            **_minimal_profile("esp.device.x"),
            "vendor": "acme",
            "product": "bulb-2",
            "product_label": "Acme Bulb 2",
            "product_labels": {"zh-Hans": "Acme 灯泡 2"},
            "vendor_label": "Acme",
            "vendor_labels": {"zh-Hans": "阿克米"},
        }

        validated = validate_profile(doc, "ok.yaml")

        assert validated["vendor"] == "acme"
        assert validated["product"] == "bulb-2"
        assert validated["product_label"] == "Acme Bulb 2"
        assert validated["product_labels"] == {"zh-Hans": "Acme 灯泡 2"}
        assert validated["vendor_label"] == "Acme"
        assert validated["vendor_labels"] == {"zh-Hans": "阿克米"}
        candidate = ProductCandidate.from_parsed(
            parse_profile_document(doc, "ok.yaml"), source="local"
        )
        assert candidate.localized_name("zh-Hans") == "Acme 灯泡 2-T1"
        assert candidate.localized_name("en") == "Acme Bulb 2-T1"
        assert candidate.localized_vendor("zh-Hans") == "阿克米"
        assert candidate.localized_vendor("en") == "Acme"

    def test_a_label_keyed_by_a_language_that_does_not_exist_is_refused(
        self,
    ) -> None:
        """A code Home Assistant never reports would silently never match."""
        doc = {
            **_minimal_profile("esp.device.x"),
            "product_labels": {"en-Hans": "Acme Thing"},
        }
        with pytest.raises(ProfileError, match="is not a Home Assistant language code"):
            validate_profile(doc, "bad.yaml")

    def test_a_regional_language_uses_the_base_language_label(self) -> None:
        """en-GB has no entry of its own; the en translation serves it."""
        doc = {
            **_minimal_profile("esp.device.x"),
            "product_label": "Acme Thing",
            "product_labels": {"en": "Acme Thing (EN)"},
            "vendor_label": "Acme",
            "vendor_labels": {"en": "Acme (EN)"},
        }
        candidate = ProductCandidate.from_parsed(
            parse_profile_document(doc, "ok.yaml"), source="local"
        )
        assert candidate.localized_label("en-GB") == "Acme Thing (EN)"
        assert candidate.localized_vendor("en-GB") == "Acme (EN)"

    def test_the_generic_model_is_spelled_with_a_star(self) -> None:
        """Writing "*" is what a per-type reference profile names, and what its devices report."""
        doc = {**_minimal_profile("esp.device.x"), "model": "*"}
        validated = validate_profile(doc, "any.yaml")
        assert validated["model"] == "*"
        candidate = ProductCandidate.from_parsed(
            parse_profile_document(doc, "any.yaml"), source="local"
        )
        assert candidate.model == MODEL_GENERIC
        assert candidate.name == f"{candidate.label}-*"

    def test_an_empty_model_is_refused(self) -> None:
        """An empty string names no model; the error says what to write."""
        doc = {**_minimal_profile("esp.device.x"), "model": ""}
        with pytest.raises(ProfileError, match=r"write the model the device reports"):
            validate_profile(doc, "empty.yaml")

    def test_a_product_without_a_model_key_is_refused(self) -> None:
        """The file has to name the model its devices report. Silence names none."""
        doc = _minimal_profile("esp.device.x")
        del doc["model"]
        with pytest.raises(
            ProfileError, match="required key not provided @ data\\['model'\\]"
        ):
            validate_profile(doc, "bad.yaml")

    def test_the_service_layer_names_no_product(self) -> None:
        """The built-in service layer is not a product: it has its own schema."""
        doc = {"version": 1, "services": _minimal_profile("esp.service.x")["devices"]}
        validated = validate_service_layer(doc, "service_layer.yaml")
        assert "model" not in validated
        with pytest.raises(ProfileError, match="required key not provided"):
            validate_profile(doc, "service_layer.yaml")

    def test_a_product_must_name_its_vendor_and_product(self) -> None:
        """The id a device is bound under is never read off a file's path."""
        for key in ("vendor", "product"):
            doc = _minimal_profile("esp.device.x")
            del doc[key]
            with pytest.raises(
                ProfileError, match=f"required key not provided @ data\\['{key}'\\]"
            ):
                validate_profile(doc, "bad.yaml")

    def test_the_hash_covers_the_model(self) -> None:
        """A corrected model number is a change every bound device must hear of."""
        a = _minimal_profile("esp.device.x")
        b = {**_minimal_profile("esp.device.x"), "model": "B2"}
        parse_profile_document(a, "a.yaml")
        parse_profile_document(b, "b.yaml")
        assert content_hash(a) != content_hash(b)

    def test_nothing_in_the_file_steers_the_choice(self) -> None:
        """A firmware hint would be the start of choosing for the user; there is none."""
        doc = {**_minimal_profile("esp.device.x"), "match": {"project_name": "x"}}
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    @pytest.mark.parametrize("vendor", ["Acme", "acme/x", "", "acme bulb"])
    def test_a_vendor_that_cannot_be_a_path_is_refused(self, vendor: str) -> None:
        """The id is a path in the repository and a key in an option."""
        doc = {**_minimal_profile("esp.device.x"), "vendor": vendor}
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_a_vendor_written_as_a_name_is_told_what_an_id_is(self) -> None:
        """A display name in the id field gets the fix, not a regular expression."""
        doc = {**_minimal_profile("esp.device.x"), "vendor": "Acme Bulb"}
        with pytest.raises(ProfileError, match="cannot be a vendor/product id"):
            validate_profile(doc, "bad.yaml")

    def test_two_blocks_for_one_type_in_one_file_are_refused(self) -> None:
        """Within a file there is nothing to choose between them."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"].append(dict(doc["devices"][0]))
        with pytest.raises(ProfileError, match="twice"):
            validate_profile(doc, "bad.yaml")

    def test_two_blocks_narrowed_to_different_names_are_fine(self) -> None:
        """Narrowing is how one type maps two devices differently."""
        doc = _minimal_profile("esp.device.x", device_name="A")
        doc["devices"].append({**doc["devices"][0], "name": "B"})
        validate_profile(doc, "ok.yaml")

    def test_the_hash_ignores_presentation_and_key_order(self) -> None:
        """Only the mapping is versioned; a new label must not read as an update."""
        base = _minimal_profile("esp.device.x")
        relabelled = {
            **_minimal_profile("esp.device.x"),
            "product_label": "New",
            "product_labels": {"zh-Hans": "新名称"},
            "vendor": "acme",
            "vendor_label": "Acme",
            "vendor_labels": {"zh-Hans": "阿克米"},
        }
        reordered = yaml.safe_load(yaml.safe_dump(_minimal_profile("esp.device.x")))
        changed = _minimal_profile("esp.device.x", entity_name="Other")
        for doc, name in (
            (base, "a.yaml"),
            (relabelled, "b.yaml"),
            (reordered, "c.yaml"),
            (changed, "d.yaml"),
        ):
            parse_profile_document(doc, name)

        assert content_hash(base) == content_hash(relabelled) == content_hash(reordered)
        assert content_hash(changed) != content_hash(base)
        assert len(content_hash(base)) == 16

    def test_a_map_mixing_key_types_still_hashes(self) -> None:
        """YAML reads ``0:`` as an int and ``on:`` as a string; both must hash.

        ``json.dumps`` cannot sort the two against each other, so the keys are
        spelled the way it would spell them first. A mapping whose keys share
        a type hashes as it always did.
        """
        typed = yaml.safe_load("devices:\n- type: x\n  map: {0: 'a', 1: 'b'}\n")
        spelled = {"devices": [{"type": "x", "map": {"0": "a", "1": "b"}}]}
        mixed = yaml.safe_load("devices:\n- type: x\n  map: {0: 'a', on: 'b'}\n")

        assert content_hash(typed) == content_hash(spelled)
        assert content_hash(mixed) != content_hash(typed)
        assert len(content_hash(mixed)) == 16

    def test_spelling_out_a_default_changes_the_hash(self) -> None:
        """The hash is of what was written; the schema's defaults do not enter it."""
        omitted = _minimal_profile("esp.device.x")
        # optional defaults to false; writing it is a different file.
        spelled = _minimal_profile("esp.device.x")
        spelled["devices"][0]["entities"][0]["attributes"]["state"]["optional"] = False
        parse_profile_document(omitted, "a.yaml")
        parse_profile_document(spelled, "b.yaml")
        assert content_hash(omitted) != content_hash(spelled)
        assert parse_profile_document(omitted, "a.yaml").hash == content_hash(omitted)

    def test_rejects_non_mapping(self) -> None:
        """A top-level list is not a profile."""
        with pytest.raises(ProfileError):
            validate_profile([], "bad.yaml")

    def test_rejects_unknown_version(self) -> None:
        """Only version 1 is understood."""
        doc = _minimal_profile("esp.device.x")
        doc["version"] = 99
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_rejects_unknown_entity_key(self) -> None:
        """Typos in entity keys fail loudly rather than being ignored."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["devcie_class"] = "switch"
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_rejects_entity_without_attributes(self) -> None:
        """An entity with no attributes cannot be bound to any parameter."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"] = {}
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_rejects_entity_without_key(self) -> None:
        """Key anchors unique_id, so omitting it must fail rather than default."""
        doc = _minimal_profile("esp.device.x")
        del doc["devices"][0]["entities"][0]["key"]
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_rejects_an_alert_on_an_attribute_that_is_not_read(self) -> None:
        """An alert watching nothing would be silently dead."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["alert"] = {
            "attribute": "alert_level",
            "levels": {"low": {"title": "T", "message": "M"}},
        }
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_accepts_an_alert_on_an_extra_attribute(self) -> None:
        """Alert levels usually ride along as detail, not as the state."""
        doc = _minimal_profile("esp.device.x")
        entity = doc["devices"][0]["entities"][0]
        entity["extra_attributes"] = {"alert_level": {"param": "Alert Level"}}
        entity["alert"] = {
            "attribute": "alert_level",
            "levels": {"low": {"title": "T", "message": "M"}},
        }

        spec = EntitySpec.from_dict(
            validate_profile(doc, "ok.yaml")["devices"][0]["entities"][0]
        )

        assert spec.alert is not None
        assert spec.alert.levels["low"].title == "T"

    def test_rejects_an_alert_with_no_levels(self) -> None:
        """An alert that can never fire is a mistake, not a no-op."""
        doc = _minimal_profile("esp.device.x")
        entity = doc["devices"][0]["entities"][0]
        entity["extra_attributes"] = {"alert_level": {"param": "Alert Level"}}
        entity["alert"] = {"attribute": "alert_level", "levels": {}}
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_auto_reset_survives_into_the_spec(self) -> None:
        """A momentary reading declares where and when it lapses back to."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["auto_reset"] = {"to": "idle", "after": 2.0}

        spec = EntitySpec.from_dict(
            validate_profile(doc, "ok.yaml")["devices"][0]["entities"][0]
        )

        assert spec.auto_reset is not None
        assert spec.auto_reset.to == "idle"
        assert spec.auto_reset.after == 2.0

    def test_rejects_a_negative_auto_reset_delay(self) -> None:
        """Resetting before the value arrives is not a thing."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["auto_reset"] = {"to": "idle", "after": -1}
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")

    def test_an_entity_declares_neither_by_default(self) -> None:
        """Both behaviours are opt-in; the plain sensors must stay plain."""
        doc = _minimal_profile("esp.device.x")

        spec = EntitySpec.from_dict(
            validate_profile(doc, "ok.yaml")["devices"][0]["entities"][0]
        )

        assert spec.auto_reset is None
        assert spec.alert is None

    def test_every_shipped_entity_declares_a_key(self) -> None:
        """No shipped entity may rely on a generated key."""
        for filename in ALL_PROFILE_FILES:
            data = yaml.safe_load((PROFILE_DIR / filename).read_text(encoding="utf-8"))
            for section in ("devices", "services"):
                for device in data.get(section) or []:
                    for entity in device["entities"]:
                        assert entity.get("key"), (
                            f"{filename} / {device['type']} / {entity['name']}"
                        )

    def test_keys_are_unique_within_a_device_type(self) -> None:
        """Two entities of one device type sharing a key would overwrite."""
        for filename in ALL_PROFILE_FILES:
            data = yaml.safe_load((PROFILE_DIR / filename).read_text(encoding="utf-8"))
            for section in ("devices", "services"):
                for device in data.get(section) or []:
                    keys = [e["key"] for e in device["entities"]]
                    assert len(keys) == len(set(keys)), (
                        f"{filename} / {device['type']} has duplicate keys"
                    )

    def test_accepts_unimplemented_platform(self) -> None:
        """Platforms without an implementation still validate."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["platform"] = "camera"
        validated = validate_profile(doc, "ok.yaml")
        entity = EntitySpec.from_dict(validated["devices"][0]["entities"][0])
        assert entity.platform not in IMPLEMENTED_PLATFORMS
        assert entity.implemented is False
        # Pending a platform, not a decision never to map it.
        assert entity.mapped is True

    def test_unmapped_platform_is_not_merely_pending(self) -> None:
        """An unmapped declaration is distinguishable from a missing platform."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["platform"] = PLATFORM_UNMAPPED
        validated = validate_profile(doc, "ok.yaml")
        entity = EntitySpec.from_dict(validated["devices"][0]["entities"][0])
        assert entity.mapped is False
        assert entity.implemented is False

    def test_unmapped_entity_may_omit_attributes(self) -> None:
        """Device types with no fixed parameter set have nothing to declare."""
        doc = _minimal_profile("esp.device.x")
        entity = doc["devices"][0]["entities"][0]
        entity["platform"] = PLATFORM_UNMAPPED
        del entity["attributes"]

        validated = validate_profile(doc, "ok.yaml")
        spec = EntitySpec.from_dict(validated["devices"][0]["entities"][0])
        assert spec.attributes == {}

    def test_structured_data_types_are_accepted(self) -> None:
        """RainMaker really reports array params, so the schema must allow them."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"]["state"]["data_type"] = "array"
        validate_profile(doc, "ok.yaml")

    def test_a_device_type_may_declare_no_entities(self) -> None:
        """``entities: []`` records a type whose parameters are all unmapped."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"] = []
        validated = validate_profile(doc, "ok.yaml")
        assert DeviceProfile.from_dict(validated["devices"][0], "ok").entities == ()

    def test_attribute_string_shorthand_expands(self) -> None:
        """``state: "Power"`` is shorthand for a param mapping."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"] = {"state": "Power"}
        validated = validate_profile(doc, "ok.yaml")
        entity = EntitySpec.from_dict(validated["devices"][0]["entities"][0])
        assert entity.attributes["state"].param == "Power"
        assert entity.attributes["state"].properties is None

    def test_properties_left_out_are_unsaid(self) -> None:
        """An attribute without properties states no access; the device's counts.

        Until the resolver has heard from the device, it is not writable.
        """
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"]["state"].pop("properties")
        validated = validate_profile(doc, "ok.yaml")
        entity = EntitySpec.from_dict(validated["devices"][0]["entities"][0])
        assert entity.attributes["state"].properties is None
        assert entity.attributes["state"].writable is False

    def test_storage_flags_are_not_profile_properties(self) -> None:
        """How the SDK stores a value is the firmware's business, not the profile's."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"]["state"]["properties"] = [
            "read",
            "persist",
        ]
        with pytest.raises(ProfileError):
            validate_profile(doc, "ok.yaml")

    def test_attributes_are_required_unless_declared_optional(self) -> None:
        """Optional is opt-in, so a plain attribute stays mandatory."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"]["extra"] = {
            "param": "Brightness",
            "optional": True,
        }
        validated = validate_profile(doc, "ok.yaml")
        entity = EntitySpec.from_dict(validated["devices"][0]["entities"][0])

        assert entity.attributes["state"].optional is False
        assert entity.attributes["extra"].optional is True

    def test_scale_requires_two_bounds(self) -> None:
        """A scale target must be exactly [low, high]."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["attributes"]["state"]["scale"] = {"to": [0]}
        with pytest.raises(ProfileError):
            validate_profile(doc, "bad.yaml")


class TestProductRegistry:
    """A registry is one product plus the service layer, and nothing else."""

    def test_a_product_supplies_its_devices(self) -> None:
        """The product's own blocks are what a device resolves against."""
        registry = ProfileRegistry.for_product(
            "acme/bulb", [_device("esp.device.fan", "acme/bulb", "A")], [], []
        )

        profile = registry.get_device("esp.device.fan")
        assert profile is not None
        assert profile.source == "acme/bulb"
        assert registry.product_id == "acme/bulb"

    def test_the_service_layer_fills_in_what_the_product_did_not_declare(self) -> None:
        """No product should have to restate the ten standard services."""
        registry = ProfileRegistry.for_product(
            "acme/bulb",
            [],
            [],
            [_device("esp.service.time", "service_layer.yaml", "Layer")],
        )

        service = registry.get_service("esp.service.time")
        assert service is not None
        assert service.source == "service_layer.yaml"

    def test_a_product_that_declares_a_service_supplies_it_itself(self) -> None:
        """Fill-in, not override: the product's block is the one found."""
        registry = ProfileRegistry.for_product(
            "acme/bulb",
            [],
            [_device("esp.service.time", "acme/bulb", "Mine")],
            [_device("esp.service.time", "service_layer.yaml", "Layer")],
        )

        service = registry.get_service("esp.service.time")
        assert service is not None
        assert service.source == "acme/bulb"
        assert service.entities[0].name == "Mine"

    def test_the_layer_never_supplies_devices(self) -> None:
        """A service type does not collide with a device type of the same name."""
        registry = ProfileRegistry.for_product(
            "acme/bulb",
            [],
            [],
            [_device("esp.shared.name", "service_layer.yaml", "Layer")],
        )

        assert registry.get_device("esp.shared.name") is None
        assert registry.get_service("esp.shared.name") is not None

    def test_describe_names_the_product(self) -> None:
        """Diagnostics has to say which product built the entities."""
        registry = ProfileRegistry.for_product(
            "acme/bulb", [_device("esp.device.fan", "acme/bulb", "A")], [], []
        )

        described = registry.describe()
        assert described["product"] == "acme/bulb"
        assert described["device_types"] == ["esp.device.fan"]


class TestNameScopedProfiles:
    """A profile entry may narrow itself to one device name."""

    @staticmethod
    def _registry(
        *devices: DeviceProfile, services: tuple[DeviceProfile, ...] = ()
    ) -> ProfileRegistry:
        return ProfileRegistry.for_product("p", list(devices), list(services), [])

    def test_named_entry_wins_for_that_name(self) -> None:
        """The device the entry names gets the narrowed mapping."""
        registry = self._registry(
            _device("esp.device.fan", "p", "Generic"),
            _device("esp.device.fan", "p", "Special", "Fan 2"),
        )

        profile = registry.get_device("esp.device.fan", "Fan 2")
        assert profile is not None
        assert profile.entities[0].name == "Special"

    def test_other_names_fall_back_to_the_nameless_entry(self) -> None:
        """A name nothing claims still resolves against the type."""
        registry = self._registry(
            _device("esp.device.fan", "p", "Generic"),
            _device("esp.device.fan", "p", "Special", "Fan 2"),
        )

        profile = registry.get_device("esp.device.fan", "Fan 1")
        assert profile is not None
        assert profile.entities[0].name == "Generic"

    def test_a_named_entry_alone_still_applies_to_that_name(self) -> None:
        """Narrowing does not require a nameless entry to exist as well."""
        registry = self._registry(_device("esp.device.fan", "p", "Special", "Fan 2"))

        assert registry.get_device("esp.device.fan", "Fan 2") is not None
        assert registry.get_device("esp.device.fan", "Fan 1") is None

    def test_lookup_without_a_name_uses_the_nameless_entry(self) -> None:
        """Callers that have no device name in hand get the general mapping."""
        registry = self._registry(
            _device("esp.device.fan", "p", "Generic"),
            _device("esp.device.fan", "p", "Special", "Fan 2"),
        )

        profile = registry.get_device("esp.device.fan")
        assert profile is not None
        assert profile.entities[0].name == "Generic"

    def test_implemented_entities_honours_the_name(self) -> None:
        """The filtered view resolves the same profile a binding would."""
        registry = self._registry(
            _device("esp.device.fan", "p", "Generic"),
            _device("esp.device.fan", "p", "Special", "Fan 2"),
        )

        entities = _implemented_entities(registry, "esp.device.fan", "Fan 2")
        assert [e.name for e in entities] == ["Special"]

    def test_services_are_narrowed_the_same_way(self) -> None:
        """Service lookups take a name too, since services have one."""
        registry = self._registry(
            services=(
                _device("esp.service.x", "p", "Generic"),
                _device("esp.service.x", "p", "Special", "X 2"),
            )
        )

        narrowed = registry.get_service("esp.service.x", "X 2")
        assert narrowed is not None
        assert narrowed.entities[0].name == "Special"

        general = registry.get_service("esp.service.x", "X 1")
        assert general is not None
        assert general.entities[0].name == "Generic"


class TestShippedProducts:
    """What Espressif ships, and how the pieces agree with each other."""

    def test_every_shipped_file_is_a_product_or_the_service_layer(self) -> None:
        """Nothing ships that the loader would not offer."""
        products, problems = scan_builtin(SOURCE_BUILTIN)

        assert problems == []
        assert {p.id for p in products} == SHIPPED_PRODUCTS
        assert set(ALL_PROFILE_FILES) == {
            SERVICE_LAYER_FILE,
            *(p.location for p in products),
        }

    def test_shipped_products_carry_their_identity_and_a_label(self) -> None:
        """A file that had to be named after its path would break when moved."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        for candidate in products:
            assert candidate.content is not None
            assert candidate.content["vendor"] == BUILTIN_VENDOR
            assert candidate.content["product"] == candidate.product
            assert candidate.label != candidate.id
            assert candidate.localized_vendor("en") == "Espressif"
            assert candidate.localized_vendor("zh-Hans") == "乐鑫"
            assert candidate.source == SOURCE_BUILTIN

    def test_the_service_layer_holds_the_ten_standard_services(self) -> None:
        """Every RainMaker node has these, so every product gets them."""
        layer = load_service_layer()

        assert sorted(s.type for s in layer) == [
            "esp.service.connectivity",
            "esp.service.groups",
            "esp.service.local_control",
            "esp.service.ota",
            "esp.service.rmaker-controller",
            "esp.service.rmaker-user-auth",
            "esp.service.scenes",
            "esp.service.schedule",
            "esp.service.system",
            "esp.service.time",
        ]

    def test_no_shipped_product_declares_a_service(self) -> None:
        """The layer is the one place services are mapped; the products defer."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        for candidate in products:
            assert candidate.content is not None
            assert candidate.content["services"] == [], candidate.id

    def test_the_shipped_products_cover_the_standard_and_extended_types(self) -> None:
        """19 useful standard devices and 16 esp_weaver extensions, as documented."""
        products, _ = scan_builtin(SOURCE_BUILTIN)

        declared = [
            block["type"]
            for candidate in products
            if candidate.content is not None
            for block in candidate.content["devices"]
        ]
        assert len(declared) == 35
        assert sorted(declared) == sorted(SHIPPED_TYPES)
        # One mapping per type across the shipped profiles.
        assert len(set(declared)) == len(declared)

    def test_every_shipped_product_has_the_whole_service_layer(self) -> None:
        """Services come from the layer, the same ten for every product."""
        registry = builtin_registry(_product_for("esp.device.switch"))

        assert registry.device_types == ["esp.device.switch"]
        assert len(registry.service_types) == 10

    def test_every_shipped_product_is_one_type_named_for_the_user(self) -> None:
        """A user whose device is one thing picks that one thing."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        by_id = {p.id: p for p in products}

        for device_type in SHIPPED_TYPES:
            candidate = by_id[_product_for(device_type)]
            assert candidate.content is not None
            assert [b["type"] for b in candidate.content["devices"]] == [device_type]
            # Named for the user, not by type: "Light", not "esp.device.light".
            assert "esp.device" not in candidate.label
            assert candidate.label.isascii()
            assert candidate.localized_label("zh-Hans") != candidate.label
            # Written per device type, not per hardware: for firmware that
            # reports "*" as its model.
            assert candidate.model == MODEL_GENERIC

    def test_the_shipped_products_are_told_apart_by_their_path(self) -> None:
        """The list key is source plus path, which is what a second copy differs in."""
        products, _ = scan_builtin(SOURCE_BUILTIN)

        assert sorted(p.key for p in products) == sorted(
            f"{SOURCE_BUILTIN}:{p.location}" for p in products
        )


class TestLocalScan:
    """Products a vendor drops in <config>/esp_weaver/."""

    def test_an_absent_directory_is_created_and_empty(self, tmp_path: Path) -> None:
        """Someone browsing their config folder should see where profiles go."""
        custom_dir = tmp_path / "esp_weaver"

        products, problems = scan_local(custom_dir, SOURCE_LOCAL)

        assert (products, problems) == ([], [])
        assert custom_dir.is_dir()

    def test_a_file_in_the_directory_is_a_product_of_its_own_vendor(
        self, tmp_path: Path
    ) -> None:
        """The file names its vendor and product; the file name says nothing."""
        (tmp_path / "mine.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )

        products, _ = scan_local(tmp_path, SOURCE_LOCAL)

        assert [p.id for p in products] == ["acme/thing"]
        assert products[0].source == SOURCE_LOCAL
        assert products[0].label == "acme/thing"
        assert products[0].location == "mine.yaml"

    def test_a_subdirectory_does_not_name_the_vendor(self, tmp_path: Path) -> None:
        """Files may be sorted into folders; the folder is not read as a vendor."""
        (tmp_path / "xiaomi").mkdir()
        (tmp_path / "xiaomi" / "bulb.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )

        products, _ = scan_local(tmp_path, SOURCE_LOCAL)

        assert [p.id for p in products] == ["acme/thing"]
        assert products[0].location == "xiaomi/bulb.yaml"

    def test_a_file_without_a_vendor_or_product_is_refused(
        self, tmp_path: Path
    ) -> None:
        """Nothing is inferred from the path; the file has to say what it is."""
        for key in ("vendor", "product"):
            doc = _minimal_profile("esp.device.aroma")
            del doc[key]
            (tmp_path / f"no_{key}.yaml").write_text(yaml.safe_dump(doc))

        products, problems = scan_local(tmp_path, SOURCE_LOCAL)

        assert products == []
        assert sorted((p.kind, p.filename) for p in problems) == [
            ("invalid", "no_product.yaml"),
            ("invalid", "no_vendor.yaml"),
        ]
        assert all(
            f"required key not provided @ data['{key}']" in problem.detail
            for key, problem in zip(
                ("product", "vendor"),
                sorted(problems, key=lambda p: p.filename),
                strict=True,
            )
        )

    def test_yml_is_read_too(self, tmp_path: Path) -> None:
        """The extension a vendor happens to type must not decide anything."""
        (tmp_path / "mine.yml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )

        products, _ = scan_local(tmp_path, SOURCE_LOCAL)
        assert [p.id for p in products] == ["acme/thing"]

    def test_other_files_in_the_directory_are_left_alone(self, tmp_path: Path) -> None:
        """A vendor's notes next to their profile are not fed to the parser."""
        (tmp_path / "notes.md").write_text("not a profile")
        (tmp_path / ".mine.yaml.123.tmp").write_text("half written")
        (tmp_path / "mine.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )

        products, problems = scan_local(tmp_path, SOURCE_LOCAL)

        assert [p.id for p in products] == ["acme/thing"]
        assert problems == []

    def test_a_broken_file_does_not_take_the_others_down(self, tmp_path: Path) -> None:
        """One bad file costs its own product, not the list."""
        (tmp_path / "a_broken.yaml").write_text(
            "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n"
        )
        (tmp_path / "b_good.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )

        products, problems = scan_local(tmp_path, SOURCE_LOCAL)

        assert [p.id for p in products] == ["acme/thing"]
        assert [(p.kind, p.filename) for p in problems] == [
            ("invalid", "a_broken.yaml")
        ]
        assert "a_broken.yaml" in problems[0].detail

    def test_a_file_that_is_not_utf8_does_not_take_the_others_down(
        self, tmp_path: Path
    ) -> None:
        """A profile re-saved in a local codepage raises UnicodeDecodeError."""
        profile = _minimal_profile("esp.device.aroma")
        profile["devices"][0]["entities"][0]["name"] = "香薰机"
        (tmp_path / "a_gbk.yaml").write_bytes(
            yaml.safe_dump(profile, allow_unicode=True).encode("gbk")
        )
        (tmp_path / "b_good.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.diffuser"))
        )

        products, problems = scan_local(tmp_path, SOURCE_LOCAL)

        assert [p.id for p in products] == ["acme/thing"]
        assert [(p.kind, p.filename) for p in problems] == [("invalid", "a_gbk.yaml")]
        assert "UTF-8" in problems[0].detail

    def test_an_unforeseen_read_failure_is_contained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever the readers did not anticipate must not reach setup."""
        (tmp_path / "a_odd.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )
        (tmp_path / "b_good.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.diffuser"))
        )

        real_parse = loader_module.parse_profile

        def explode(raw: str, name: str) -> Any:
            if name.endswith("a_odd.yaml"):
                raise MemoryError("out of nowhere")
            return real_parse(raw, name)

        monkeypatch.setattr(loader_module, "parse_profile", explode)

        products, problems = scan_local(tmp_path, SOURCE_LOCAL)

        assert [p.id for p in products] == ["acme/thing"]
        assert [(p.kind, p.filename) for p in problems] == [("invalid", "a_odd.yaml")]

    def test_a_profile_left_in_the_integration_directory_is_offered_and_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It works today and disappears on update, so it has to be said."""
        (tmp_path / "stray.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.stray"))
        )
        monkeypatch.setattr(loader_module, "PROFILE_DIR", tmp_path)

        products, problems = scan_builtin(SOURCE_BUILTIN)

        assert [p.id for p in products] == ["acme/thing"]
        assert [(p.kind, p.filename) for p in problems] == [("misplaced", "stray.yaml")]

    def test_an_unreadable_stray_file_is_not_also_called_misplaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two repairs for one file, one of them saying it works, would contradict."""
        (tmp_path / "stray.yaml").write_text(
            "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n"
        )
        monkeypatch.setattr(loader_module, "PROFILE_DIR", tmp_path)

        products, problems = scan_builtin(SOURCE_BUILTIN)

        assert products == []
        assert [p.kind for p in problems] == ["invalid"]


class TestImplementedFiltering:
    """Only platforms esp_weaver has an implementation for produce entities."""

    def test_every_shipped_device_type_produces_entities(self) -> None:
        """A declaration nothing can build is a silent no-op, so there are none."""
        registry = shipped_registry()
        for device_type in registry.device_types:
            declared = registry.get_device(device_type)
            assert declared is not None
            if not any(entity.mapped for entity in declared.entities):
                continue
            assert _implemented_entities(registry, device_type), device_type

    def test_unimplemented_entities_are_dropped(self) -> None:
        """A platform without an implementation yields nothing."""
        doc = _minimal_profile("esp.device.x")
        doc["devices"][0]["entities"][0]["platform"] = "camera"
        validated = validate_profile(doc, "camera.yaml")
        devices, services = profile_blocks(validated, "camera.yaml")
        registry = ProfileRegistry.for_product("camera", devices, services, [])

        assert _implemented_entities(registry, "esp.device.x") == []

    def test_implemented_entities_survive(self) -> None:
        """A sensor device type still yields entities."""
        registry = shipped_registry()
        entities = _implemented_entities(registry, "esp.device.sensor")
        assert entities
        assert all(e.platform in IMPLEMENTED_PLATFORMS for e in entities)

    def test_unknown_device_type_yields_nothing(self) -> None:
        """An unmapped type is not an error."""
        registry = shipped_registry()
        assert _implemented_entities(registry, "esp.device.nonexistent") == []

    def test_a_small_product_knows_only_its_own_types(self) -> None:
        """Picking the switch product does not bring the whole model along."""
        registry = builtin_registry("espressif/switch")

        assert registry.device_types == ["esp.device.switch"]
        assert len(registry.service_types) == 10


class TestShippedEntityCategories:
    """Where the shipped profiles put their entities in the UI.

    The category comes from the YAML rather than from the platform code, so it
    is the declarations that have to be checked.
    """

    def _shipped(self, platform: str) -> list[EntitySpec]:
        """Return every implemented entity the built-ins declare for one platform."""
        registry = shipped_registry()
        return [
            entity
            for device_type in registry.device_types
            for entity in _implemented_entities(registry, device_type)
            if entity.platform == platform
        ]

    def test_number_entities_are_config_entities(self) -> None:
        """Thresholds and intervals are settings, not dashboard controls."""
        numbers = self._shipped("number")

        assert numbers
        assert [
            entity.key
            for entity in numbers
            if entity.entity_category != EntityCategory.CONFIG.value
        ] == []

    def test_sensor_entities_are_not_categorised(self) -> None:
        """A reading is what the device is for, so it belongs on the dashboard."""
        sensors = self._shipped("sensor")

        assert sensors
        assert [
            entity.key for entity in sensors if entity.entity_category is not None
        ] == []


class TestCatalogAndRepairs:
    """The catalog offers what can be found, and says what could not be read."""

    @pytest.fixture(autouse=True)
    def _config_dir(self, own_config_dir: Path) -> None:
        """Write profiles somewhere this test owns, not the shared config."""

    @staticmethod
    def _write(hass: HomeAssistant, body: str, name: str = "mine.yaml") -> Path:
        """Put a vendor profile in the configuration directory."""
        custom_dir = Path(hass.config.path(CUSTOM_PROFILE_DIR))
        custom_dir.mkdir(exist_ok=True)
        path = custom_dir / name
        path.write_text(body)
        return path

    async def test_the_config_directory_is_where_vendor_profiles_are_read(
        self, hass: HomeAssistant
    ) -> None:
        """<config>/esp_weaver/ is the documented path, so pin it here."""
        await hass.async_add_executor_job(
            self._write, hass, yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )

        catalog = await async_get_catalog(hass)

        [mine] = catalog.by_id("acme/thing")
        assert mine.source == SOURCE_LOCAL
        assert mine.key == f"{SOURCE_LOCAL}:mine.yaml"
        assert catalog.by_id(_product_for("esp.device.switch"))

    async def test_the_catalog_is_cached_until_refreshed(
        self, hass: HomeAssistant
    ) -> None:
        """Repeated lookups reuse the same object; a refresh reads again."""
        first = await async_get_catalog(hass)
        second = await async_get_catalog(hass)
        third = await async_refresh_catalog(hass, online=False)

        assert first is second
        assert first is not third
        assert set(first.candidates) == set(third.candidates)

    async def test_a_local_copy_of_a_shipped_product_is_listed_beside_it(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing is hidden: both copies are offered, and the user picks."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        switch = next(p for p in products if p.id == "espressif/switch")
        assert switch.content is not None
        edited = {**switch.content, "product_label": "My switch"}
        await hass.async_add_executor_job(
            self._write, hass, yaml.safe_dump(edited), "switch.yaml"
        )

        catalog = await async_get_catalog(hass)

        copies = catalog.by_id("espressif/switch")
        assert [(c.source, c.label) for c in copies] == [
            (SOURCE_LOCAL, "My switch"),
            (SOURCE_BUILTIN, switch.label),
        ]

    async def test_an_unusable_profile_raises_a_repair(
        self, hass: HomeAssistant
    ) -> None:
        """Editing over Samba means the log is not somewhere they will look."""
        await hass.async_add_executor_job(
            self._write,
            hass,
            "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n",
        )

        await async_get_catalog(hass)

        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, "profile_file_invalid_esp_weaver_mine.yaml"
        )
        assert issue is not None
        assert issue.severity == ir.IssueSeverity.WARNING
        assert issue.translation_placeholders is not None
        assert issue.translation_placeholders["filename"] == "mine.yaml"

    async def test_fixing_the_file_clears_the_repair(self, hass: HomeAssistant) -> None:
        """A repair that outlives its cause is worse than none."""
        await hass.async_add_executor_job(
            self._write,
            hass,
            "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n",
        )
        await async_get_catalog(hass)

        await hass.async_add_executor_job(
            self._write, hass, yaml.safe_dump(_minimal_profile("esp.device.aroma"))
        )
        await async_refresh_catalog(hass, online=False)

        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN, "profile_file_invalid_esp_weaver_mine.yaml"
            )
            is None
        )

    async def test_a_profile_in_the_integration_directory_raises_a_repair(
        self,
        stray_profiles_load: None,
        hass: HomeAssistant,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The file works today and disappears on update, so it has to be said."""
        (tmp_path / "stray.yaml").write_text(
            yaml.safe_dump(_minimal_profile("esp.device.stray"))
        )
        monkeypatch.setattr(loader_module, "PROFILE_DIR", tmp_path)

        catalog = await async_get_catalog(hass)

        assert catalog.by_id("acme/thing")
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"profile_file_misplaced_{tmp_path.name}_stray.yaml"
        )
        assert issue is not None
        assert issue.translation_placeholders is not None
        assert issue.translation_placeholders["custom_dir"] == CUSTOM_PROFILE_DIR

    async def test_a_clean_load_raises_nothing(self, hass: HomeAssistant) -> None:
        """The shipped profiles on their own are not a problem."""
        await async_get_catalog(hass)

        assert [
            issue_id
            for domain, issue_id in ir.async_get(hass).issues
            if domain == DOMAIN
        ] == []

    async def test_the_repository_is_not_read_unless_asked(
        self, hass: HomeAssistant
    ) -> None:
        """The offline catalog is what setup uses, and setup must not wait on the network."""
        catalog = await async_get_catalog(hass)

        assert catalog.online is False
        assert catalog.index_error == ""

    def test_a_candidate_describes_itself_without_its_content(self) -> None:
        """Diagnostics lists the products; it does not dump every mapping."""
        candidate = ProductCandidate.from_parsed(
            parse_profile_document(
                {
                    **_minimal_profile("esp.device.x"),
                    "product_label": "X",
                    "product": "x",
                },
                "x.yaml",
            ),
            source=SOURCE_LOCAL,
        )

        described = candidate.describe()
        assert described["id"] == "acme/x"
        assert "content" not in described
        assert described["hash"] == candidate.hash


def test_schema_is_reusable() -> None:
    """Validation does not mutate the schema between calls."""
    doc = _minimal_profile("esp.device.x")
    validate_profile(dict(doc), "a.yaml")
    validate_profile(dict(doc), "b.yaml")


def test_bounds_reject_missing_max() -> None:
    """Bounds must declare both ends of the range."""
    doc = _minimal_profile("esp.device.x")
    doc["devices"][0]["entities"][0]["attributes"]["state"]["bounds"] = {"min": 0}
    with pytest.raises(ProfileError):
        validate_profile(doc, "bad.yaml")


def test_voluptuous_error_is_wrapped() -> None:
    """Schema failures surface as ProfileError, not vol.Invalid."""
    with pytest.raises(ProfileError):
        validate_profile({"version": 1, "model": "T1", "devices": [{}]}, "bad.yaml")
    assert not isinstance(ProfileError("x"), vol.Invalid)
