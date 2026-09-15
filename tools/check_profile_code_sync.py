# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Keeps the two copies of the version 1 profile logic identical.

The schema, content hash, product categories and index format are implemented
twice: in the integration (``custom_components/esp_weaver/profiles/``), which
loads downloaded profiles, and in the profile repository
(``espressif/esp-weaver-profiles``, ``tools/profilelib/``), whose CI judges
the files and builds ``index.json`` without Home Assistant. The copies agree
today only if nobody edits one without the other; this tool says when they
have drifted, and can copy the integration's modules over the repository's.

Run from the integration's root against a checkout of the repository::

    .venv/bin/python tools/check_profile_code_sync.py --repository ../esp-weaver-profiles
    .venv/bin/python tools/check_profile_code_sync.py --repository ../esp-weaver-profiles --sync

``--repository`` defaults to ``$ESP_WEAVER_PROFILES``, then to a
``profiles_repo/`` directory here, then to ``../esp-weaver-profiles`` (the
same default as ``tools/check_shipped_profiles.py``).

Without ``--sync`` nothing is written: the tool lists every module that
differs or is missing on one side, and exits non-zero if there is any.
``--sync`` copies the integration's modules into the repository checkout;
commit the result there. The comparison is byte for byte, comments included:
the modules are copies, so any difference is drift. ``__init__.py`` is not
compared - the two packages legitimately differ there.

Exit codes: 0 when the copies agree, 1 when they differ, 2 when the path
given is not a checkout of the profile repository.
"""

import argparse
import os
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
# The modules that exist, identically, on both sides.
MODULES = ("schema.py", "product.py", "index.py")
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "esp_weaver" / "profiles"
REPOSITORY_DIR = Path("tools") / "profilelib"


def default_repository() -> Path:
    """Return where a checkout of the profile repository is expected."""
    if named := os.environ.get("ESP_WEAVER_PROFILES"):
        return Path(named)
    staged = REPO_ROOT / "profiles_repo"
    if (staged / REPOSITORY_DIR).is_dir():
        return staged
    return REPO_ROOT.parent / "esp-weaver-profiles"


def differences(integration: Path, repository: Path) -> list[str]:
    """Return one line per module the two directories disagree on."""
    lines: list[str] = []
    for name in MODULES:
        mine = integration / name
        theirs = repository / name
        if not mine.is_file() and not theirs.is_file():
            lines.append(f"{name}: missing from both")
        elif not theirs.is_file():
            lines.append(f"{name}: in the integration only")
        elif not mine.is_file():
            lines.append(f"{name}: in the repository only")
        elif mine.read_bytes() != theirs.read_bytes():
            lines.append(f"{name}: differs")
    return lines


def sync(integration: Path, repository: Path) -> list[str]:
    """Copy the integration's modules over the repository's; return what changed."""
    changed = differences(integration, repository)
    for name in MODULES:
        source = integration / name
        target = repository / name
        if source.is_file() and (
            not target.exists() or target.read_bytes() != source.read_bytes()
        ):
            shutil.copyfile(source, target)
    return changed


def main(argv: list[str] | None = None) -> int:
    """Run the check, or the sync, and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--sync",
        action="store_true",
        help="copy the integration's modules into the repository checkout",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=default_repository(),
        help="a checkout of the profile repository (default: %(default)s)",
    )
    parser.add_argument(
        "--integration", type=Path, default=INTEGRATION_DIR, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    repository = args.repository / REPOSITORY_DIR
    if not repository.is_dir():
        print(
            f"{args.repository} is not a checkout of the profile repository: "
            f"no {REPOSITORY_DIR}/",
            file=sys.stderr,
        )
        return 2

    if args.sync:
        for line in sync(args.integration, repository):
            print(f"synced {line}")
        return 0

    lines = differences(args.integration, repository)
    for line in lines:
        print(line)
    if lines:
        print(
            f"{len(lines)} module(s) differ between {args.integration} and "
            f"{repository}; edit both copies, or run "
            "tools/check_profile_code_sync.py --sync and commit the repository"
        )
        return 1
    print("profile code copies are identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
