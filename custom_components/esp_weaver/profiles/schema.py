# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Validation schema and typed views for device profile YAML.

A profile documents the full RainMaker model, wider than Home Assistant can
show, so two kinds of declaration are accepted and skipped at resolve time: a
platform outside :data:`IMPLEMENTED_PLATFORMS` (not built yet), and
:data:`PLATFORM_UNMAPPED` (no Home Assistant equivalent; ``Schedules``, the
local-control ``POP``).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Final

import voluptuous as vol
import yaml

# Platforms that have a working platform file and can produce entities today.
# Anything else validates fine but is skipped during binding resolution.
IMPLEMENTED_PLATFORMS: Final[frozenset[str]] = frozenset(
    {
        "alarm_control_panel",
        "binary_sensor",
        "button",
        "climate",
        "cover",
        "date",
        "datetime",
        "event",
        "fan",
        "humidifier",
        "lawn_mower",
        "light",
        "lock",
        "media_player",
        "number",
        "remote",
        "select",
        "sensor",
        "siren",
        "switch",
        "text",
        "time",
        "vacuum",
        "valve",
        "water_heater",
    }
)

# A parameter deliberately not mapped to any entity; may omit `attributes`.
PLATFORM_UNMAPPED: Final[str] = "none"

# Only version 1 exists. Bumping is reserved for incompatible key changes.
SUPPORTED_VERSION: Final[int] = 1

YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yaml", ".yml"})

# `array` and `object` appear only on declarations that map to no entity.
DATA_TYPES: Final[frozenset[str]] = frozenset(
    {"array", "bool", "float", "int", "object", "string"}
)
# A profile states who may read or write a parameter; how the SDK stores it
# (``persist``, ``time_series``) is the firmware's business and not declared.
PARAM_PROPERTIES: Final[frozenset[str]] = frozenset({"read", "write"})


class ProfileError(Exception):
    """Raised when a profile file is malformed or conflicts with another."""


def _expand_attribute_shorthand(value: Any) -> Any:
    """Allow ``state: "Power"`` as shorthand for ``state: {param: "Power"}``."""
    if isinstance(value, str):
        return {"param": value}
    return value


BOUNDS_SCHEMA: Final = vol.Schema(
    {
        vol.Required("min"): vol.Any(int, float),
        vol.Required("max"): vol.Any(int, float),
        vol.Optional("step"): vol.Any(int, float),
    }
)

SCALE_SCHEMA: Final = vol.Schema(
    {
        vol.Required("to"): vol.All([vol.Any(int, float)], vol.Length(min=2, max=2)),
    }
)

ATTRIBUTE_SCHEMA: Final = vol.All(
    _expand_attribute_shorthand,
    vol.Schema(
        {
            vol.Required("param"): vol.All(str, vol.Length(min=1)),
            vol.Optional("type"): str,
            vol.Optional("data_type"): vol.In(DATA_TYPES),
            vol.Optional("ui_type"): str,
            # Left out, the access is whatever the device reports.
            vol.Optional("properties"): [vol.In(PARAM_PROPERTIES)],
            # A capability the entity gains only if the device has it.
            vol.Optional("optional", default=False): bool,
            vol.Optional("bounds"): BOUNDS_SCHEMA,
            vol.Optional("scale"): SCALE_SCHEMA,
            vol.Optional("map"): dict,
            vol.Optional("options"): [str],
            vol.Optional("invert"): bool,
            vol.Optional("state_when"): dict,
        }
    ),
)


AUTO_RESET_SCHEMA: Final = vol.Schema(
    {
        vol.Required("to"): vol.Any(str, int, float, bool),
        vol.Required("after"): vol.All(vol.Any(int, float), vol.Range(min=0)),
    }
)

ALERT_LEVEL_SCHEMA: Final = vol.Schema(
    {
        vol.Required("title"): vol.All(str, vol.Length(min=1)),
        vol.Required("message"): vol.All(str, vol.Length(min=1)),
    }
)

ALERT_SCHEMA: Final = vol.Schema(
    {
        vol.Required("attribute"): vol.All(str, vol.Length(min=1)),
        vol.Required("levels"): vol.All({str: ALERT_LEVEL_SCHEMA}, vol.Length(min=1)),
    }
)

THRESHOLD_END_MIN: Final[str] = "min"
THRESHOLD_END_MAX: Final[str] = "max"
THRESHOLD_ENDS: Final[frozenset[str]] = frozenset(
    {THRESHOLD_END_MIN, THRESHOLD_END_MAX}
)

# Marks a number as one end of the range a sensor in the same device block
# may read within; the sensor fires esp_weaver_alert when it passes.
THRESHOLD_SCHEMA: Final = vol.Schema(
    {
        vol.Required("of"): vol.All(str, vol.Length(min=1)),
        vol.Required("end"): vol.In(THRESHOLD_ENDS),
    }
)


def _require_attributes(entity: dict[str, Any]) -> dict[str, Any]:
    """Reject an entity that declares no parameter to read; unmapped ones are exempt."""
    if entity["platform"] != PLATFORM_UNMAPPED and not entity["attributes"]:
        raise vol.Invalid(f"entity {entity['name']!r} declares no attributes")
    return entity


def _require_alert_attribute(entity: dict[str, Any]) -> dict[str, Any]:
    """Reject an alert that watches an attribute the entity never reads."""
    alert = entity.get("alert")
    if alert is None:
        return entity

    known = {*entity["attributes"], *(entity.get("extra_attributes") or {})}
    if alert["attribute"] not in known:
        raise vol.Invalid(
            f"entity {entity['name']!r} alerts on {alert['attribute']!r}, "
            f"which is not one of its attributes"
        )
    return entity


ENTITY_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("platform"): vol.All(str, vol.Length(min=1)),
            vol.Required("name"): vol.All(str, vol.Length(min=1)),
            # Anchors the entity's unique_id, so it must never change once
            # shipped. `name` is only a display label and can be edited freely.
            vol.Required("key"): vol.All(str, vol.Length(min=1)),
            vol.Optional("icon"): str,
            vol.Optional("device_class"): str,
            vol.Optional("state_class"): str,
            vol.Optional("entity_category"): vol.In({"config", "diagnostic"}),
            vol.Optional("unit"): str,
            vol.Optional("display_precision"): vol.All(int, vol.Range(min=0)),
            vol.Required("attributes", default=dict): {str: ATTRIBUTE_SCHEMA},
            vol.Optional("extra_attributes"): {str: ATTRIBUTE_SCHEMA},
            # Momentary states (a gesture) return to `to` after `after` seconds.
            vol.Optional("auto_reset"): AUTO_RESET_SCHEMA,
            # Fires an esp_weaver_alert event when the watched attribute
            # enters one of the declared levels, and another on leaving it.
            vol.Optional("alert"): ALERT_SCHEMA,
            # Only on a number: which sensor of this device block it bounds.
            vol.Optional("threshold"): THRESHOLD_SCHEMA,
        }
    ),
    _require_attributes,
    _require_alert_attribute,
)


def _check_thresholds(device: dict[str, Any]) -> dict[str, Any]:
    """Reject a threshold that bounds nothing, or bounds one end twice."""
    sensors = {e["key"] for e in device["entities"] if e["platform"] == "sensor"}
    claimed: set[tuple[str, str]] = set()
    for entity in device["entities"]:
        threshold = entity.get("threshold")
        if threshold is None:
            continue
        if entity["platform"] != "number":
            raise vol.Invalid(
                f"entity {entity['name']!r} declares a threshold but is a "
                f"{entity['platform']}, not a number"
            )
        if threshold["of"] not in sensors:
            raise vol.Invalid(
                f"entity {entity['name']!r} bounds sensor {threshold['of']!r}, "
                f"which {device['type']} does not declare"
            )
        pair = (threshold["of"], threshold["end"])
        if pair in claimed:
            raise vol.Invalid(
                f"sensor {threshold['of']!r} has two {threshold['end']} thresholds"
            )
        claimed.add(pair)
    return device


DEVICE_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("type"): vol.All(str, vol.Length(min=1)),
            vol.Optional("name"): str,
            vol.Required("entities"): [ENTITY_SCHEMA],
        }
    ),
    _check_thresholds,
)

# Vendor and product ids: lower case, no separators, so they read the same in
# a URL, a filename and an attribute.
_SLUG_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def _slug(value: Any) -> str:
    """Return a vendor/product id, or say in plain words why it is not one.

    These ids are stored with the device and used in repository paths, so
    they cannot carry spaces or capitals. A name written that way is a
    display name and belongs in ``vendor_label`` / ``product_label``; the
    bare regular expression this replaces left authors guessing at that.
    """
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise vol.Invalid(
            f"{value!r} cannot be a vendor/product id: use lowercase letters "
            "and digits, with '_', '-' or '.' inside, starting with a letter or "
            "digit and with spaces written as '_' (for example "
            "'espressif_test'). The id is stored with the device and used in "
            "repository paths, so it never changes; write the name as it "
            "should be shown in vendor_label / product_label instead"
        )
    return value


SLUG: Final = _slug


def _reject_duplicate_blocks(profile: dict[str, Any]) -> dict[str, Any]:
    """Refuse two blocks for one ``(type, name)`` in the same file."""
    for section in ("devices", "services"):
        seen: set[tuple[str, str | None]] = set()
        for block in profile.get(section) or []:
            key = (block["type"], block.get("name"))
            if key in seen:
                raise vol.Invalid(
                    f"{section} declares {block['type']!r}"
                    f"{' named ' + repr(block['name']) if block.get('name') else ''}"
                    " twice"
                )
            seen.add(key)
    return profile


# The ``model`` Espressif's per-type reference profiles name, and what a device
# built on them reports as its model; see fit.py. Shown after the label in the
# list, ``Light-*``.
MODEL_GENERIC: Final = "*"
PROFILE_LABEL: Final = vol.All(str, vol.Length(min=1, max=120))

# The language tags Home Assistant can be configured with, copied from
# homeassistant/generated/languages.py. A labels key outside this set can
# never match a configured language, so it is refused here rather than
# silently never used; refresh the copy when Home Assistant adds a language.
LANGUAGES: Final[frozenset[str]] = frozenset(
    {
        "af",
        "ar",
        "bg",
        "bn",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "el",
        "en",
        "en-GB",
        "eo",
        "es",
        "es-419",
        "et",
        "eu",
        "fa",
        "fi",
        "fr",
        "fy",
        "ga",
        "gl",
        "gsw",
        "he",
        "hi",
        "hr",
        "hu",
        "hy",
        "id",
        "is",
        "it",
        "ja",
        "ka",
        "ko",
        "lb",
        "lt",
        "lv",
        "mk",
        "ml",
        "nb",
        "nl",
        "nn",
        "pl",
        "pt",
        "pt-BR",
        "ro",
        "ru",
        "sk",
        "sl",
        "sq",
        "sr",
        "sr-Latn",
        "sv",
        "ta",
        "te",
        "th",
        "tr",
        "uk",
        "ur",
        "vi",
        "zh-Hans",
        "zh-Hant",
    }
)


def _language(value: Any) -> str:
    """Return a labels key, or say why it names no Home Assistant language."""
    if not isinstance(value, str) or value not in LANGUAGES:
        raise vol.Invalid(
            f"{value!r} is not a Home Assistant language code: use 'en' for "
            "English, 'zh-Hans' for Simplified Chinese, 'zh-Hant' for "
            "Traditional Chinese; a code Home Assistant never reports "
            "(for example 'en-Hans') would silently never match"
        )
    return value


PROFILE_LABELS: Final = {_language: PROFILE_LABEL}


def _model(value: Any) -> str:
    """The model the device reports, byte for byte; an empty string is refused."""
    if not isinstance(value, str):
        raise vol.Invalid("expected str")
    if not value:
        raise vol.Invalid(
            f'write the model the device reports, "{MODEL_GENERIC}" for one built '
            "on the reference profiles"
        )
    if len(value) > 60:
        raise vol.Invalid("length of value must be at most 60")
    return value


PROFILE_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("version"): vol.In({SUPPORTED_VERSION}),
            # Who makes the product and what it is: together the id the file
            # is listed, bound and stored under. Never inferred from where the
            # file sits, so moving or renaming it changes nothing.
            vol.Required("vendor"): SLUG,
            vol.Required("product"): SLUG,
            # Display forms of the vendor id. Like product labels, these do
            # not affect matching or the content hash.
            vol.Optional("vendor_label"): PROFILE_LABEL,
            vol.Optional("vendor_labels"): PROFILE_LABELS,
            # What the user sees in the list, in place of the bare id.
            vol.Optional("product_label"): PROFILE_LABEL,
            # Optional localized product display names. They do not affect
            # matching or the content hash.
            vol.Optional("product_labels"): PROFILE_LABELS,
            # The model the product's devices report, byte for byte; "*" is
            # what Espressif's per-type profiles name. Required rather than
            # defaulted so a file says what it is for. See fit.py.
            vol.Required("model"): _model,
            vol.Optional("devices", default=list): [DEVICE_SCHEMA],
            vol.Optional("services", default=list): [DEVICE_SCHEMA],
        }
    ),
    _reject_duplicate_blocks,
)

# The shipped service layer is not a product: it maps the services every node
# has, and names no vendor, product or model.
SERVICE_LAYER_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("version"): vol.In({SUPPORTED_VERSION}),
            vol.Optional("services", default=list): [DEVICE_SCHEMA],
        }
    ),
    _reject_duplicate_blocks,
)


@dataclass(frozen=True, slots=True)
class AttributeSpec:
    """One HA attribute backed by one RainMaker parameter."""

    name: str
    param: str
    param_type: str | None = None
    data_type: str | None = None
    ui_type: str | None = None
    # None until the resolver fills it from the device: the profile said nothing.
    properties: tuple[str, ...] | None = None
    optional: bool = False
    bounds: dict[str, Any] | None = None
    scale: dict[str, Any] | None = None
    value_map: dict[Any, Any] | None = None
    options: tuple[str, ...] | None = None
    invert: bool = False
    state_when: dict[str, Any] | None = None

    @property
    def writable(self) -> bool:
        """Return True if the device accepts writes to this parameter."""
        return self.properties is not None and "write" in self.properties

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "AttributeSpec":
        """Build a spec from an already-validated attribute mapping."""
        options = data.get("options")
        properties = data.get("properties")
        return cls(
            name=name,
            param=data["param"],
            param_type=data.get("type"),
            data_type=data.get("data_type"),
            ui_type=data.get("ui_type"),
            properties=tuple(properties) if properties is not None else None,
            optional=bool(data.get("optional", False)),
            bounds=data.get("bounds"),
            scale=data.get("scale"),
            value_map=data.get("map"),
            options=tuple(options) if options else None,
            invert=bool(data.get("invert", False)),
            state_when=data.get("state_when"),
        )


@dataclass(frozen=True, slots=True)
class AutoResetSpec:
    """A state the entity falls back to once a momentary one has been shown."""

    to: Any
    after: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoResetSpec":
        """Build a spec from an already-validated auto_reset mapping."""
        return cls(to=data["to"], after=float(data["after"]))


@dataclass(frozen=True, slots=True)
class AlertLevelSpec:
    """What to say when the watched attribute reaches one level."""

    title: str
    message: str


@dataclass(frozen=True, slots=True)
class AlertSpec:
    """Turns one attribute's levels into esp_weaver_alert events on the bus."""

    attribute: str
    levels: dict[str, AlertLevelSpec]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlertSpec":
        """Build a spec from an already-validated alert mapping."""
        return cls(
            attribute=data["attribute"],
            levels={
                level: AlertLevelSpec(title=body["title"], message=body["message"])
                for level, body in data["levels"].items()
            },
        )


@dataclass(frozen=True, slots=True)
class ThresholdSpec:
    """Names the sensor a number bounds, and which end of its range it is."""

    of: str
    end: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThresholdSpec":
        """Build a spec from an already-validated threshold mapping."""
        return cls(of=data["of"], end=data["end"])


@dataclass(frozen=True, slots=True)
class EntitySpec:
    """One Home Assistant entity declared by a device profile."""

    platform: str
    name: str
    key: str
    icon: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    unit: str | None = None
    display_precision: int | None = None
    attributes: dict[str, AttributeSpec] = field(default_factory=dict)
    extra_attributes: dict[str, AttributeSpec] = field(default_factory=dict)
    auto_reset: AutoResetSpec | None = None
    alert: AlertSpec | None = None
    threshold: ThresholdSpec | None = None

    @property
    def implemented(self) -> bool:
        """Return True if this entity's platform can be built today."""
        return self.platform in IMPLEMENTED_PLATFORMS

    @property
    def mapped(self) -> bool:
        """Return False if this parameter is deliberately not an entity."""
        return self.platform != PLATFORM_UNMAPPED

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EntitySpec":
        """Build a spec from an already-validated entity mapping."""
        return cls(
            platform=data["platform"],
            name=data["name"],
            key=data["key"],
            icon=data.get("icon"),
            device_class=data.get("device_class"),
            state_class=data.get("state_class"),
            entity_category=data.get("entity_category"),
            unit=data.get("unit"),
            display_precision=data.get("display_precision"),
            attributes={
                name: AttributeSpec.from_dict(name, attr)
                for name, attr in data["attributes"].items()
            },
            extra_attributes={
                name: AttributeSpec.from_dict(name, attr)
                for name, attr in (data.get("extra_attributes") or {}).items()
            },
            auto_reset=(
                AutoResetSpec.from_dict(data["auto_reset"])
                if data.get("auto_reset")
                else None
            ),
            alert=AlertSpec.from_dict(data["alert"]) if data.get("alert") else None,
            threshold=(
                ThresholdSpec.from_dict(data["threshold"])
                if data.get("threshold")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """All entities declared for one RainMaker device or service type."""

    type: str
    entities: tuple[EntitySpec, ...]
    name: str | None = None
    source: str = "<unknown>"

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str) -> "DeviceProfile":
        """Build a profile from an already-validated device mapping."""
        return cls(
            type=data["type"],
            name=data.get("name"),
            entities=tuple(EntitySpec.from_dict(e) for e in data["entities"]),
            source=source,
        )


def _validate(
    data: Any, source: str, schema: Callable[[Any], dict[str, Any]]
) -> dict[str, Any]:
    """Validate one parsed YAML document against ``schema``, naming ``source``."""
    if not isinstance(data, dict):
        raise ProfileError(f"{source}: expected a mapping at the top level")
    try:
        validated: dict[str, Any] = schema(data)
    except vol.Invalid as err:
        raise ProfileError(f"{source}: {err}") from err
    return validated


def validate_profile(data: Any, source: str) -> dict[str, Any]:
    """Validate one parsed product profile, named by ``source`` in any error.

    Raises:
        ProfileError: The document does not match the schema.
    """
    # Checked ahead of the schema: voluptuous reports a rejected dict key
    # only as "extra keys not allowed", which would bury the reason.
    if isinstance(data, dict):
        for labels_field in ("vendor_labels", "product_labels"):
            labels = data.get(labels_field)
            if isinstance(labels, dict):
                for code in labels:
                    try:
                        _language(code)
                    except vol.Invalid as err:
                        raise ProfileError(f"{source}: {labels_field}: {err}") from err
    return _validate(data, source, PROFILE_SCHEMA)


Validator = Callable[[Any, str], dict[str, Any]]


def _load_yaml(raw: str, name: str) -> Any:
    """Parse YAML held in memory, naming ``name`` in any error.

    Raises:
        ProfileError: The text is not YAML.
    """
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as err:
        raise ProfileError(f"Invalid YAML in profile {name}: {err}") from err


def parse_profile_text(
    raw: str, name: str, validator: Validator = validate_profile
) -> dict[str, Any]:
    """Parse and validate profile YAML held in memory.

    Raises:
        ProfileError: The text is not YAML, or not a profile.
    """
    return validator(_load_yaml(raw, name), name)


@dataclass(frozen=True, slots=True)
class ParsedProfile:
    """A product profile that passed validation, and the hash of what was written."""

    validated: dict[str, Any]
    hash: str


def parse_profile_document(document: Any, name: str) -> ParsedProfile:
    """Validate a parsed product profile, then hash it as written.

    Raises:
        ProfileError: The document is not a profile; nothing is hashed then.
    """
    validated = validate_profile(document, name)
    return ParsedProfile(validated, content_hash(document))


def parse_profile(raw: str, name: str) -> ParsedProfile:
    """Parse, validate and hash product profile YAML held in memory.

    Raises:
        ProfileError: The text is not YAML, or not a profile.
    """
    return parse_profile_document(_load_yaml(raw, name), name)


def validate_service_layer(data: Any, source: str) -> dict[str, Any]:
    """Validate the parsed service layer, named by ``source`` in any error.

    Raises:
        ProfileError: The document does not match the schema.
    """
    return _validate(data, source, SERVICE_LAYER_SCHEMA)


# Hashed over the mapping and the model, not the vendor or label: those only
# say how a product is presented, while the model decides which devices it
# fits (see ``fit``), so correcting it must reach the devices bound to it.
_HASHED_SECTIONS: Final[tuple[str, ...]] = ("devices", "services")
_HASHED_MODEL: Final = "model"
HASH_LENGTH: Final[int] = 16


def _json_key(key: Any) -> str:
    """Spell a mapping key the way ``json.dumps`` would.

    YAML reads ``0:``, ``true:`` and ``null:`` as typed keys. ``json.dumps``
    spells each of them, but sorts keys before spelling them and cannot order
    an ``int`` against a ``str``, so a ``map`` mixing the two would raise.
    Spelling them first keeps the hash of every single-typed mapping as it
    was and gives a mixed one a hash at all.
    """
    if isinstance(key, str):
        return key
    if key is True:
        return "true"
    if key is False:
        return "false"
    if key is None:
        return "null"
    return str(key)


def _with_json_keys(value: Any) -> Any:
    """Return ``value`` with every mapping key, at any depth, spelled as JSON."""
    if isinstance(value, dict):
        return {_json_key(key): _with_json_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_with_json_keys(item) for item in value]
    return value


def content_hash(document: dict[str, Any]) -> str:
    """Return the fingerprint of a profile's mapping and model, as written.

    Over the parsed YAML before validation, so the hash depends on the file
    alone and every release computes the same one: key order and comments do
    not change it, the schema's defaults do not enter it. Validate first; a
    document that is not a profile has no hash worth keeping.
    """
    hashed = {section: document.get(section) or [] for section in _HASHED_SECTIONS}
    hashed[_HASHED_MODEL] = str(document.get(_HASHED_MODEL, ""))
    canonical = json.dumps(
        _with_json_keys(hashed),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        # YAML can parse a scalar into what JSON has no type for; the schema
        # refuses those where they matter, so this only has to be stable.
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def profile_blocks(
    validated: dict[str, Any], source: str
) -> tuple[list[DeviceProfile], list[DeviceProfile]]:
    """Return the device and service profiles a validated document declares."""
    return (
        [DeviceProfile.from_dict(d, source) for d in validated.get("devices", [])],
        [DeviceProfile.from_dict(s, source) for s in validated.get("services", [])],
    )
