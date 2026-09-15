# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Matches profiles against a node's reported config to produce bindings.

Devices and services are looked up by ``type`` (``esp.device.switch``) and,
where a profile narrows on it, by ``name``; parameters by ``name``. All exact.
What a profile states about a parameter - its type, data type, access, bounds,
options - must be what the node's config reports for it; on any difference the
entity is not created and the difference is logged. What the profile leaves
unsaid is taken from the node. An entity missing any declared parameter is
skipped rather than half-built.

Keys are made unique per node: the first device of a type keeps the plain key,
later ones are narrowed with their device name.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from difflib import get_close_matches
import logging
import re
from typing import Any, Final
import unicodedata

from ..helpers.once import log_once
from .binding import EntityBinding, ParamBinding, ThresholdRole, primary_attribute
from .registry import ProfileRegistry
from .schema import THRESHOLD_ENDS, AttributeSpec, DeviceProfile, EntitySpec

_LOGGER = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Turn a device name into a unique_id suffix without depending on HA."""
    if not text:
        return "unknown"
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "unknown"


# Attribute name that, when present, supplies the entity's device_class at
# runtime instead of the value declared on the entity.
DEVICE_CLASS_ATTRIBUTE: Final[str] = "device_class"

# Key in a node's parameter config listing the allowed values of a string param.
VALID_STRINGS_KEY: Final[str] = "valid_strs"

# Properties the SDK puts in a node config that say how a value is stored, not
# who may read or write it. A profile's ``properties`` states access only, so
# these are not held against it. ``simple_ts`` is the SDK's spelling for a
# simple time series parameter.
STORAGE_PROPERTIES: Final[frozenset[str]] = frozenset(
    {"time_series", "simple_ts", "persist"}
)


@dataclass(frozen=True, slots=True)
class SpecConflict:
    """One thing the profile states about a parameter that the node contradicts."""

    attribute: str
    param: str
    field: str
    declared: Any
    reported: Any

    def describe(self) -> str:
        """Return the conflict in one line, for a log or a form."""
        return (
            f"{self.field} of {self.param!r}: the profile declares "
            f"{self.declared!r}, the device reports {self.reported!r}"
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the conflict as plain data, for diagnostics."""
        return {
            "attribute": self.attribute,
            "param": self.param,
            "field": self.field,
            "declared": self.declared,
            "reported": self.reported,
        }


# Why an entity the profile declares produced nothing; only the last two are
# worth acting on.
REASON_UNMAPPED: Final = "no_ha_equivalent"
REASON_PLATFORM: Final = "platform_not_implemented"
REASON_CONFLICT: Final = "parameter_conflict"
REASON_MISSING_PARAMS: Final = "missing_parameters"


@dataclass
class Resolution:
    """What a resolution left out, and why, for a rehearsal to spell out.

    The resolver logs its decisions; this is the same decisions kept, so
    that a preview says exactly what setup would do without re-deriving it.
    ``unknown_types`` are the devices and services no profile declares;
    ``skipped`` the entities a declared profile could not build on this node.
    """

    unknown_types: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def skip(
        self,
        device_type: str,
        entity: EntitySpec,
        reason: str,
        **detail: Any,
    ) -> None:
        """Record one entity that produced nothing."""
        self.skipped.append(
            {
                "device_type": device_type,
                "name": entity.name,
                "key": entity.key,
                "reason": reason,
                **detail,
            }
        )


def _access_properties(properties: Iterable[str]) -> tuple[str, ...]:
    """Return the properties that govern reading and writing, in a fixed order."""
    kept = {name for name in properties if name not in STORAGE_PROPERTIES}
    return tuple(sorted(kept))


def _reported_properties(runtime: dict[str, Any]) -> tuple[str, ...] | None:
    """Return the access the node reports for a parameter, or None if it says nothing."""
    reported = runtime.get("properties")
    if reported is None:
        return None
    return _access_properties(str(name) for name in reported)


def _reported_bounds(runtime: dict[str, Any]) -> dict[str, Any] | None:
    """Return the bounds the node reports, or None if it reports none."""
    reported = runtime.get("bounds")
    if not isinstance(reported, dict) or not reported:
        return None
    return dict(reported)


def _reported_options(runtime: dict[str, Any]) -> tuple[str, ...] | None:
    """Return the values the node allows for a string parameter, or None."""
    reported = runtime.get(VALID_STRINGS_KEY)
    if not reported:
        return None
    return tuple(str(value) for value in reported)


def _reported_text(runtime: dict[str, Any], key: str) -> str | None:
    """Return one string the node reports under ``key``, or None."""
    value = runtime.get(key)
    return str(value) if value else None


def _same_bounds(declared: dict[str, Any], reported: dict[str, Any]) -> bool:
    """Say whether two bounds name the same ends and step, as numbers."""
    if set(declared) != set(reported):
        return False
    try:
        return all(float(declared[key]) == float(reported[key]) for key in declared)
    except (TypeError, ValueError):
        return False


def spec_conflicts(
    attribute: str, spec: AttributeSpec, runtime: dict[str, Any]
) -> list[SpecConflict]:
    """Return every field the profile states about a parameter that the node contradicts.

    Only fields both sides speak to are compared; a field the profile leaves out
    is not a conflict, and is filled from the node by :func:`_complete_spec`.
    The order of ``options`` is not held against either side.
    """
    conflicts: list[SpecConflict] = []

    def differs(name: str, declared: Any, reported: Any, same: bool) -> None:
        if declared is not None and reported is not None and not same:
            conflicts.append(
                SpecConflict(attribute, spec.param, name, declared, reported)
            )

    reported_type = _reported_text(runtime, "type")
    differs("type", spec.param_type, reported_type, spec.param_type == reported_type)

    reported_data_type = _reported_text(runtime, "data_type")
    differs(
        "data_type",
        spec.data_type,
        reported_data_type,
        spec.data_type == reported_data_type,
    )

    reported_ui_type = _reported_text(runtime, "ui_type")
    differs("ui_type", spec.ui_type, reported_ui_type, spec.ui_type == reported_ui_type)

    reported_properties = _reported_properties(runtime)
    declared_properties = (
        _access_properties(spec.properties) if spec.properties is not None else None
    )
    differs(
        "properties",
        list(declared_properties) if declared_properties is not None else None,
        list(reported_properties) if reported_properties is not None else None,
        declared_properties == reported_properties,
    )

    reported_bounds = _reported_bounds(runtime)
    differs(
        "bounds",
        spec.bounds,
        reported_bounds,
        spec.bounds is not None
        and reported_bounds is not None
        and _same_bounds(spec.bounds, reported_bounds),
    )

    reported_options = _reported_options(runtime)
    differs(
        "options",
        list(spec.options) if spec.options is not None else None,
        list(reported_options) if reported_options is not None else None,
        spec.options is not None
        and reported_options is not None
        and set(spec.options) == set(reported_options),
    )

    return conflicts


def _complete_spec(spec: AttributeSpec, runtime: dict[str, Any]) -> AttributeSpec:
    """Fill what the profile left unsaid about a parameter from the node's config.

    Called only once :func:`spec_conflicts` found nothing, so every field the
    profile did state is what the node reports, and a field it did not state
    has one answer: the node's.
    """
    updates: dict[str, Any] = {}

    if spec.param_type is None and (value := _reported_text(runtime, "type")):
        updates["param_type"] = value
    if spec.data_type is None and (value := _reported_text(runtime, "data_type")):
        updates["data_type"] = value
    if spec.ui_type is None and (value := _reported_text(runtime, "ui_type")):
        updates["ui_type"] = value
    if spec.properties is None and (props := _reported_properties(runtime)) is not None:
        updates["properties"] = props
    if spec.bounds is None and (bounds := _reported_bounds(runtime)) is not None:
        updates["bounds"] = bounds
    if spec.options is None and (options := _reported_options(runtime)) is not None:
        updates["options"] = options

    if not updates:
        return spec

    # replace() rather than listing every field: a new field on AttributeSpec
    # then carries over on its own instead of being silently dropped here.
    return replace(spec, **updates)


def _resolve_options(
    attributes: dict[str, ParamBinding],
) -> tuple[str, ...] | None:
    """Pick the option list an entity offers, as Home Assistant sees it.

    Same rule for a select's state and a climate's mode attribute: a ``map``
    wins, otherwise the enumerated values - the profile's, which are the
    firmware's own where it reported them, or the firmware's where the
    profile declared none.
    """
    primary = primary_attribute(attributes)
    return primary.ha_options if primary is not None else None


def _resolve_device_class(
    entity: EntitySpec,
    attributes: dict[str, ParamBinding],
    device_values: dict[str, Any],
) -> str | None:
    """Return the device_class, letting a reported parameter override it."""
    binding = attributes.get(DEVICE_CLASS_ATTRIBUTE)
    if binding is None:
        return entity.device_class

    reported = device_values.get(binding.param)
    if reported is None:
        return entity.device_class

    translated = binding.to_ha(reported)
    if not isinstance(translated, str) or not translated:
        _LOGGER.debug(
            "Device reported an unusable device_class %r for %r; keeping %r",
            reported,
            entity.name,
            entity.device_class,
        )
        return entity.device_class

    return translated


def entity_conflicts(
    entity: EntitySpec, param_index: dict[str, dict[str, Any]]
) -> list[SpecConflict]:
    """Return everything the profile states about the entity's parameters that the node contradicts.

    Over the attributes and the extra attributes alike; a parameter the node
    does not report cannot conflict.
    """
    conflicts: list[SpecConflict] = []
    for attr_name, spec in (
        *entity.attributes.items(),
        *entity.extra_attributes.items(),
    ):
        runtime = param_index.get(spec.param)
        if runtime is not None:
            conflicts.extend(spec_conflicts(attr_name, spec, runtime))
    return conflicts


def _bind_attributes(
    specs: dict[str, AttributeSpec],
    param_index: dict[str, dict[str, Any]],
    device_name: str,
    required: bool,
) -> dict[str, ParamBinding] | None:
    """Bind a group of attributes, or return None if a required one is absent.

    An ``optional`` attribute (a light's colour) never disqualifies the entity.
    Called once the specs are known to agree with the node.
    """
    bound: dict[str, ParamBinding] = {}
    for attr_name, spec in specs.items():
        runtime = param_index.get(spec.param)
        if runtime is None:
            if required and not spec.optional:
                return None
            continue
        bound[attr_name] = ParamBinding(
            device_name=device_name,
            attribute=attr_name,
            spec=_complete_spec(spec, runtime),
        )
    return bound


def _resolve_entity(
    node_id: str,
    device_name: str,
    device_type: str,
    entity: EntitySpec,
    param_index: dict[str, dict[str, Any]],
    device_values: dict[str, Any],
    resolution: Resolution,
) -> EntityBinding | None:
    """Resolve one profile entity against a node, or None if unusable.

    Whatever is left out is recorded in ``resolution`` with its reason.
    """
    if not entity.mapped:
        _LOGGER.debug(
            "Node %s: %r is documented as having no Home Assistant equivalent",
            node_id,
            entity.name,
        )
        resolution.skip(device_type, entity, REASON_UNMAPPED)
        return None

    if not entity.implemented:
        _LOGGER.debug(
            "Node %s: skipping %r, platform %s is not implemented",
            node_id,
            entity.name,
            entity.platform,
        )
        resolution.skip(device_type, entity, REASON_PLATFORM, platform=entity.platform)
        return None

    if conflicts := entity_conflicts(entity, param_index):
        # Loud: an entity built on a description the device contradicts would
        # misbehave in ways nothing else explains.
        log_once(
            _LOGGER,
            logging.ERROR,
            "Node %s: %r of %s is not created, because the profile and the "
            "device disagree about its parameters: %s. Correct the profile to "
            "match the firmware, or the firmware to match the profile",
            node_id,
            entity.name,
            device_type,
            "; ".join(conflict.describe() for conflict in conflicts),
        )
        resolution.skip(
            device_type,
            entity,
            REASON_CONFLICT,
            conflicts=[conflict.as_dict() for conflict in conflicts],
            details=[conflict.describe() for conflict in conflicts],
        )
        return None

    attributes = _bind_attributes(
        entity.attributes, param_index, device_name, required=True
    )
    if attributes is None:
        missing = _unreported_params(entity, param_index)
        log_once(
            _LOGGER,
            logging.INFO,
            "Node %s: %r of %s needs %s, which %r does not report, so the entity "
            "is not created. Parameter names are matched exactly",
            node_id,
            entity.name,
            device_type,
            ", ".join(repr(param) for param in missing),
            device_name,
        )
        resolution.skip(device_type, entity, REASON_MISSING_PARAMS, missing=missing)
        return None

    extra = (
        _bind_attributes(
            entity.extra_attributes, param_index, device_name, required=False
        )
        or {}
    )

    return EntityBinding(
        node_id=node_id,
        device_name=device_name,
        device_type=device_type,
        platform=entity.platform,
        name=entity.name,
        key=entity.key,
        icon=entity.icon,
        device_class=_resolve_device_class(entity, attributes, device_values),
        state_class=entity.state_class,
        entity_category=entity.entity_category,
        unit=entity.unit,
        display_precision=entity.display_precision,
        options=_resolve_options(attributes),
        attributes=attributes,
        extra_attributes=extra,
        auto_reset=entity.auto_reset,
        alert=entity.alert,
        threshold=(
            ThresholdRole(entity.threshold.of, entity.threshold.end)
            if entity.threshold
            else None
        ),
    )


def _link_thresholds(bindings: list[EntityBinding]) -> list[EntityBinding]:
    """Wire the sensors and threshold numbers of one device instance together.

    After every entity of it is resolved, so an end the firmware lacks gets no
    dangling reference.
    """
    by_sensor: dict[str, dict[str, str]] = {}
    for binding in bindings:
        if binding.threshold is not None:
            by_sensor.setdefault(binding.threshold.sensor_key, {})[
                binding.threshold.end
            ] = binding.key

    if not by_sensor:
        return bindings

    linked: list[EntityBinding] = []
    for binding in bindings:
        if binding.key in by_sensor and binding.platform == "sensor":
            linked.append(replace(binding, thresholds=by_sensor[binding.key]))
        elif (role := binding.threshold) is not None:
            ends = by_sensor[role.sensor_key]
            (other,) = THRESHOLD_ENDS - {role.end}
            linked.append(
                replace(binding, threshold=replace(role, partner_key=ends.get(other)))
            )
        else:
            linked.append(binding)
    return linked


def params_by_name(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index a node-config device's params by name, skipping malformed ones."""
    return {
        param["name"]: param
        for param in entry.get("params") or []
        if isinstance(param, dict) and "name" in param
    }


def _unreported_params(
    entity: EntitySpec, param_index: dict[str, dict[str, Any]]
) -> list[str]:
    """Return the required parameters of an entity the node did not report, sorted."""
    return sorted(
        {
            spec.param
            for spec in entity.attributes.values()
            if not spec.optional and spec.param not in param_index
        }
    )


def _nearest_type(device_type: str, known: list[str]) -> str | None:
    """Return the known type a missed one was most likely meant to be.

    Tells a type nobody wrote a profile for from one off by a capital letter.
    """
    close = get_close_matches(device_type, known, n=1, cutoff=0.8)
    return close[0] if close else None


def _resolve_section(
    node_id: str,
    entries: Any,
    values: dict[str, Any],
    lookup: Callable[[str, str | None], DeviceProfile | None],
    known_types: list[str],
    label: str,
    resolution: Resolution,
) -> list[EntityBinding]:
    """Resolve one ``devices`` or ``services`` list of a node's config."""
    bindings: list[EntityBinding] = []

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue

        device_type = entry.get("type", "")
        device_name = entry.get("name", "")
        profile = lookup(device_type, device_name)

        if profile is None:
            # Loud: the device sets up with no entities and nothing else says why.
            nearest = _nearest_type(device_type, known_types)
            log_once(
                _LOGGER,
                logging.WARNING,
                "Node %s: no profile declares %s type %r, so %r contributes no "
                "entities.%s",
                node_id,
                label,
                device_type,
                device_name,
                f" Did you mean {nearest!r}?" if nearest else "",
            )
            resolution.unknown_types.append(
                {"kind": label, "name": device_name, "type": device_type}
            )
            continue

        param_index = params_by_name(entry)
        device_values = values.get(device_name) or {}

        resolved: list[EntityBinding] = []
        for entity in profile.entities:
            binding = _resolve_entity(
                node_id=node_id,
                device_name=device_name,
                device_type=device_type,
                entity=entity,
                param_index=param_index,
                device_values=device_values,
                resolution=resolution,
            )
            if binding is not None:
                resolved.append(binding)
        bindings.extend(_link_thresholds(resolved))

    return bindings


def _disambiguate(node_id: str, bindings: list[EntityBinding]) -> list[EntityBinding]:
    """Give every binding on one node a key of its own.

    A node may carry two devices of one type (a two-gang switch), and the key is
    the unique_id. The first binding to claim a key keeps it, so existing
    entities are never renamed; later ones are narrowed with the device's name,
    which also becomes their entity name. Where the name is no more
    distinguishing than the key, both fall back to a number: garage_door_lock_2,
    "... Lock 2".
    """
    keys: set[str] = set()
    names: set[str] = set()
    result: list[EntityBinding] = []

    for binding in bindings:
        if binding.key not in keys:
            keys.add(binding.key)
            names.add(binding.name)
            result.append(binding)
            continue

        slug = _slugify(binding.device_name)
        # Without this a garage door lock would repeat itself, as its device name
        # slugifies to the very key it is colliding with.
        key_stem = slug if slug.startswith(binding.key) else f"{binding.key}_{slug}"
        name_stem = binding.device_name or binding.name
        key, name = key_stem, name_stem
        attempt = 2
        while key in keys or name in names:
            key = f"{key_stem}_{attempt}"
            name = f"{name_stem} {attempt}"
            attempt += 1

        _LOGGER.debug(
            "Node %s: %r of %s already claims %r, filing %r under %r as %r instead",
            node_id,
            binding.name,
            binding.device_type,
            binding.key,
            binding.device_name,
            key,
            name,
        )
        keys.add(key)
        names.add(name)
        result.append(replace(binding, key=key, name=name))

    return _rekey_thresholds(bindings, result)


def _rekey_thresholds(
    before: list[EntityBinding], after: list[EntityBinding]
) -> list[EntityBinding]:
    """Point threshold links at the keys their targets ended up with."""
    renamed = {
        (old.device_name, old.key): new.key
        for old, new in zip(before, after, strict=True)
        if old.key != new.key
    }
    if not renamed:
        return after

    def final(device_name: str, key: str) -> str:
        return renamed.get((device_name, key), key)

    result: list[EntityBinding] = []
    for binding in after:
        if binding.thresholds:
            binding = replace(
                binding,
                thresholds={
                    end: final(binding.device_name, key)
                    for end, key in binding.thresholds.items()
                },
            )
        if (role := binding.threshold) is not None:
            binding = replace(
                binding,
                threshold=ThresholdRole(
                    final(binding.device_name, role.sensor_key),
                    role.end,
                    final(binding.device_name, role.partner_key)
                    if role.partner_key
                    else None,
                ),
            )
        result.append(binding)
    return result


def resolve_bindings(
    node_id: str,
    node_config: dict[str, Any],
    params: dict[str, Any] | None,
    registry: ProfileRegistry,
    resolution: Resolution | None = None,
) -> list[EntityBinding]:
    """Produce every entity binding a node's config supports.

    Services are resolved like devices, against the same parameter payload;
    only the profile lookup differs, since the two type namespaces are separate.

    Args:
        node_config: ``devices`` and optionally ``services``, each a list of
            ``{name, type, params: [...]}``.
        params: Current values, ``{device_name: {param: value}}``, read for
            entity metadata.
        resolution: Where to record what was left out and why, for a caller
            that wants to say so; setup passes nothing and reads the log.

    Returns:
        Bindings on implemented platforms whose declared parameters all exist
        on the node.
    """
    values = params or {}
    if resolution is None:
        resolution = Resolution()

    bindings = _resolve_section(
        node_id,
        node_config.get("devices"),
        values,
        registry.get_device,
        registry.device_types,
        "device",
        resolution,
    )
    bindings += _resolve_section(
        node_id,
        node_config.get("services"),
        values,
        registry.get_service,
        registry.service_types,
        "service",
        resolution,
    )
    bindings = _disambiguate(node_id, bindings)

    _LOGGER.debug("Node %s: resolved %d entity bindings", node_id, len(bindings))
    return bindings
