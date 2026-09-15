# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Checks the entity keys the device profiles declare.

A profile entity's ``key`` becomes the suffix of its ``unique_id``, so two
entities sharing a key under one device type overwrite each other, and changing
a key orphans the existing entity along with its history and any automation
referencing it.

Run without arguments to list the declared keys and catch duplicated ones (the
schema already refuses a missing key). Pass a live registry to also see which of its entities the profiles no
longer account for, which is the check a test cannot perform::

    .venv/bin/python tools/check_profile_keys.py
    .venv/bin/python tools/check_profile_keys.py --registry path/to/core.entity_registry
"""

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.esp_weaver.identity import DOMAIN  # noqa: E402
from custom_components.esp_weaver.profiles.loader import (  # noqa: E402
    PROFILE_DIR,
    SERVICE_LAYER_FILE,
    builtin_registry,
    load_service_layer,
    scan_builtin,
)
from custom_components.esp_weaver.profiles.product import SOURCE_BUILTIN  # noqa: E402
from custom_components.esp_weaver.profiles.registry import ProfileRegistry  # noqa: E402
from custom_components.esp_weaver.profiles.schema import (  # noqa: E402
    DeviceProfile,
    profile_blocks,
)


def profile_keys_by_type() -> dict[tuple[str, str], list[str]]:
    """Return declared keys grouped by (file, device type).

    Every shipped file is read through the integration's own loader, so what
    this lists is exactly what the integration would build.
    """
    blocks: list[tuple[str, DeviceProfile]] = [
        (SERVICE_LAYER_FILE, profile) for profile in load_service_layer()
    ]
    products, problems = scan_builtin(SOURCE_BUILTIN)
    for problem in problems:
        print(f"!! {problem.path.relative_to(PROFILE_DIR)}: {problem.kind}")
    for candidate in products:
        if candidate.content is None or candidate.path is None:
            continue
        devices, services = profile_blocks(candidate.content, candidate.id)
        shown = str(candidate.path.relative_to(PROFILE_DIR))
        blocks.extend((shown, profile) for profile in devices + services)

    result: dict[tuple[str, str], list[str]] = {}
    for filename, profile in blocks:
        keys = [entity.key for entity in profile.entities if entity.implemented]
        if keys:
            result[(filename, profile.type)] = keys
    return result


def profile_entity_keys() -> frozenset[str]:
    """Return the keys of every entity the shipped profiles can build today.

    One registry per shipped product, since a device is bound to one product;
    the union is what any registry entry could have come from.
    """
    keys: set[str] = set()
    products, _ = scan_builtin(SOURCE_BUILTIN)
    for candidate in products:
        registry: ProfileRegistry = builtin_registry(candidate.id)
        for types, get in (
            (registry.device_types, registry.get_device),
            (registry.service_types, registry.get_service),
        ):
            for device_type in types:
                profile = get(device_type)
                if profile is None:
                    continue
                keys.update(e.key for e in profile.entities if e.implemented)
    return frozenset(keys)


def load_registry_suffixes(path: Path) -> set[str]:
    """Return unique_id suffixes of esp_weaver entities in a live registry."""
    data = json.loads(path.read_text(encoding="utf-8"))
    prefix = f"{DOMAIN}_"
    suffixes: set[str] = set()
    for entity in data["data"]["entities"]:
        if entity.get("platform") != DOMAIN:
            continue
        unique_id = entity.get("unique_id", "")
        if not unique_id.startswith(prefix):
            continue
        # unique_id is {domain}_{node_id}_{key}; node ids contain no underscores
        remainder = unique_id[len(prefix) :]
        _, _, key = remainder.partition("_")
        if key:
            suffixes.add(key)
    return suffixes


def main() -> int:
    """Print the declared keys and return a non-zero code when they look unsafe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        help="Path to a live core.entity_registry to compare against",
    )
    args = parser.parse_args()

    declared_by_type = profile_keys_by_type()

    print("=== Keys declared by each profile ===")
    for (filename, device_type), keys in declared_by_type.items():
        print(f"\n  {filename} / {device_type}  ({len(keys)} entities)")
        for key in keys:
            print(f"    {key}")

    if args.registry:
        baseline = load_registry_suffixes(args.registry)
        declared = set(profile_entity_keys())

        print(f"\n=== Compared with the live registry {args.registry} ===")
        print(
            f"  {len(baseline)} unique_id suffixes in the registry, {len(declared)} profile keys"
        )

        for label, keys in (
            ("kept", sorted(baseline & declared)),
            ("gone", sorted(baseline - declared)),
            ("new", sorted(declared - baseline)),
        ):
            print(f"\n  {label} ({len(keys)})")
            for key in keys:
                print(f"    {key}")

    failed = False
    for (filename, device_type), keys in declared_by_type.items():
        repeated = sorted({k for k in keys if keys.count(k) > 1})
        if repeated:
            failed = True
            print(
                f"\n!! {filename} / {device_type} repeats a key; the entities overwrite each other:"
            )
            for key in repeated:
                print(f"    {key}")

    # The same key under two device types only collides if one node reports both.
    owners: dict[str, list[str]] = {}
    for (_, device_type), keys in declared_by_type.items():
        for key in keys:
            owners.setdefault(key, []).append(device_type)
    shared = {k: v for k, v in owners.items() if len(set(v)) > 1}
    if shared:
        print("\nNote: the keys below are shared by several device types.")
        print("      They collide only when one node reports those types together.")
        for key, types in sorted(shared.items()):
            print(f"    {key:32s} {sorted(set(types))}")

    if failed:
        return 1

    print("\nNo key is repeated within a device type.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
