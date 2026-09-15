# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Read-only rehearsal of what the profile path would build for a node.

Runs the resolver against the config a node reported and spells out what it
left out and why, which setup only logs. Nothing here creates or changes an
entity.
"""

from typing import Any

from ..identity import entity_unique_id
from .binding import EntityBinding
from .registry import ProfileRegistry
from .resolver import (
    REASON_CONFLICT,
    REASON_MISSING_PARAMS,
    REASON_PLATFORM,
    REASON_UNMAPPED,
    Resolution,
    params_by_name,
    resolve_bindings,
)

__all__ = [
    "REASON_CONFLICT",
    "REASON_MISSING_PARAMS",
    "REASON_PLATFORM",
    "REASON_UNMAPPED",
    "build_preview",
    "compare_unique_ids",
    "describe_binding",
    "describe_reported",
    "setup_report",
    "unique_id",
]


def unique_id(binding: EntityBinding) -> str:
    """Return the registry unique_id this binding would claim."""
    return entity_unique_id(binding.node_id, binding.key)


def _entries(node_config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return every device and service a node reported, tagged with its kind."""
    return [
        (kind, entry)
        for kind in ("device", "service")
        for entry in node_config.get(f"{kind}s") or []
        if isinstance(entry, dict)
    ]


def describe_reported(node_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarise the device types and parameter names a node reported."""
    return [
        {
            "kind": kind,
            "name": entry.get("name", ""),
            "type": entry.get("type", ""),
            "params": list(params_by_name(entry)),
        }
        for kind, entry in _entries(node_config)
    ]


def describe_binding(binding: EntityBinding) -> dict[str, Any]:
    """Summarise one resolved binding for a human reading diagnostics."""
    return {
        "unique_id": unique_id(binding),
        "platform": binding.platform,
        "name": binding.name,
        "key": binding.key,
        "device_name": binding.device_name,
        "device_type": binding.device_type,
        "writable": binding.writable,
        "params": {
            attribute: param.param for attribute, param in binding.attributes.items()
        },
        "extra_params": {
            attribute: param.param
            for attribute, param in binding.extra_attributes.items()
        },
    }


def compare_unique_ids(
    bindings: list[EntityBinding],
    existing: set[str],
) -> dict[str, list[str]]:
    """Diff the unique_ids the profile would claim against the live registry.

    ``only_in_registry`` are the entities a rebind would lose.
    """
    predicted = {unique_id(binding) for binding in bindings}
    return {
        "matched": sorted(predicted & existing),
        "only_in_bindings": sorted(predicted - existing),
        "only_in_registry": sorted(existing - predicted),
    }


def setup_report(preview: dict[str, Any]) -> dict[str, Any]:
    """Reduce a preview to what is worth telling someone adding a device.

    Leaves out the skip reasons nobody can act on (unmapped parameter, platform
    not built) and keeps the ones that mean firmware and profile disagree.

    Returns:
        ``created``, ``skipped`` and ``unknown_types`` lists, names only.
        ``skipped`` holds the entities left out over a parameter, each with
        its ``reason``.
    """
    skipped = preview.get("skipped") or {}
    return {
        "created": preview.get("entities") or [],
        "skipped": [
            entity
            for entity in skipped.get("entities") or []
            if entity.get("reason") in (REASON_CONFLICT, REASON_MISSING_PARAMS)
        ],
        "unknown_types": skipped.get("device_types") or [],
    }


def build_preview(
    registry: ProfileRegistry,
    node_id: str,
    node_config: dict[str, Any],
    params: dict[str, Any] | None = None,
    existing_unique_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Assemble the profile section of a diagnostics dump: names only, no values.

    With ``existing_unique_ids`` the result also carries a ``comparison``
    against the entity registry.
    """
    resolution = Resolution()
    bindings = resolve_bindings(node_id, node_config, params, registry, resolution)

    preview: dict[str, Any] = {
        "registry": registry.describe(),
        "reported": describe_reported(node_config),
        "entities": [describe_binding(binding) for binding in bindings],
        "skipped": {
            "device_types": resolution.unknown_types,
            "entities": resolution.skipped,
        },
    }

    if existing_unique_ids is not None:
        preview["comparison"] = compare_unique_ids(bindings, existing_unique_ids)

    return preview
