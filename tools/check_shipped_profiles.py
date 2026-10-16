# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Keeps the shipped Espressif profiles identical to the profile repository's.

Espressif's products live in the profile repository
(``espressif/esp-weaver-profiles``, ``vendors/espressif/``) like any vendor's.
The integration also ships a copy of them,
``custom_components/esp_weaver/device_profiles/espressif/``, so that they are
listed on a Home Assistant that has never reached the repository. Two copies
drift; this tool says when they have, and brings the shipped copy up to date.

Run from the integration's root against a checkout of the repository::

    .venv/bin/python tools/check_shipped_profiles.py --repository ../esp-weaver-profiles
    .venv/bin/python tools/check_shipped_profiles.py --repository ../esp-weaver-profiles --sync

``--repository`` defaults to ``$ESP_WEAVER_PROFILES``, then to a
``profiles_repo/`` directory here (where the repository was staged before it
had a repository of its own), then to ``../esp-weaver-profiles``.

Without ``--sync`` nothing is written: the tool lists every file that differs,
is missing on one side, or is present on one side only, and exits non-zero
if there is any. ``--sync`` makes the shipped directory an exact copy of the
repository's: files are copied byte for byte and files the repository no longer
has are removed. Maintainers run the sync by hand when the repository changes;
``tests/profiles/test_shipped_sync.py`` runs the check when a checkout is at
hand.
"""

import argparse
import os
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
# Espressif's directory within the profile repository.
ESPRESSIF_DIR = Path("vendors") / "espressif"
SHIPPED_DIR = REPO_ROOT / "custom_components" / "esp_weaver" / "device_profiles"
SHIPPED_DIR = SHIPPED_DIR / "espressif"
YAML_SUFFIXES = frozenset({".yaml", ".yml"})


def default_repository() -> Path:
    """Return where a checkout of the profile repository is expected."""
    if named := os.environ.get("ESP_WEAVER_PROFILES"):
        return Path(named)
    staged = REPO_ROOT / "profiles_repo"
    if (staged / ESPRESSIF_DIR).is_dir():
        return staged
    return REPO_ROOT.parent / "esp-weaver-profiles"


def _files(directory: Path) -> dict[str, Path]:
    """Return the profile files in ``directory`` by name."""
    return {
        path.name: path
        for path in directory.iterdir()
        if path.is_file() and path.suffix in YAML_SUFFIXES
    }


def differences(repository: Path, shipped: Path) -> list[str]:
    """Return one line per file the two directories disagree on.

    Byte for byte, deliberately stricter than the content hash: the hash is
    over the parsed content, so a reformatted file hashes the same - but the
    shipped files are a copy of the repository's, so any byte difference,
    whitespace included, is drift worth reporting.
    """
    in_repository = _files(repository)
    in_shipped = _files(shipped)
    lines: list[str] = []
    for name in sorted(in_repository.keys() | in_shipped.keys()):
        if name not in in_shipped:
            lines.append(f"{name}: in the repository only")
        elif name not in in_repository:
            lines.append(f"{name}: shipped only, not in the repository")
        elif in_repository[name].read_bytes() != in_shipped[name].read_bytes():
            lines.append(f"{name}: differs")
    return lines


def sync(repository: Path, shipped: Path) -> list[str]:
    """Make ``shipped`` an exact copy of ``repository``; return what changed."""
    changed = differences(repository, shipped)
    in_repository = _files(repository)
    for name, path in _files(shipped).items():
        if name not in in_repository:
            path.unlink()
    for name, path in in_repository.items():
        target = shipped / name
        if not target.exists() or target.read_bytes() != path.read_bytes():
            shutil.copyfile(path, target)
    return changed


def main(argv: list[str] | None = None) -> int:
    """Run the check, or the sync, and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--sync",
        action="store_true",
        help="make the shipped copy identical to the repository's",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=default_repository(),
        help="a checkout of the profile repository (default: %(default)s)",
    )
    parser.add_argument(
        "--shipped", type=Path, default=SHIPPED_DIR, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    repository = args.repository / ESPRESSIF_DIR
    if not repository.is_dir():
        print(
            f"{args.repository} is not a checkout of the profile repository: "
            f"no {ESPRESSIF_DIR}/",
            file=sys.stderr,
        )
        return 2

    if args.sync:
        for line in sync(repository, args.shipped):
            print(f"synced {line}")
        return 0

    lines = differences(repository, args.shipped)
    for line in lines:
        print(line)
    if lines:
        print(
            f"{len(lines)} file(s) differ between {repository} and "
            f"{args.shipped}; run tools/check_shipped_profiles.py --sync"
        )
        return 1
    print("shipped profiles match the repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
