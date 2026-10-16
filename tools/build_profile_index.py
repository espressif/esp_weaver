# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Builds the ``index.json`` of the profile repository.

The profile repository (``espressif/esp-weaver-profiles``) holds one YAML per
product under ``vendors/<vendor>/<product>.yaml`` and nothing else the
integration reads except the index this writes. The index is what the
integration downloads to show the list, so it carries everything the list
needs - vendor, product, label, model, category - and the content hash the
integration checks the downloaded file against.

This tool judges every file by the integration's own ``profiles/index.py``,
which also reads both indexes back: a file this accepts is one the
integration will list and load. The profile repository does not run this
copy; it runs its own (``tools/build_index.sh`` there, on
``tools/profilelib/``), because it needs only PyYAML and voluptuous, not
Home Assistant and not a checkout of this repository. The two copies of the
profile logic are kept byte for byte identical by
``tools/check_profile_code_sync.py``, so the two tools accept and refuse
exactly the same files. Point it at the repository's root::

    python tools/build_profile_index.py ../esp-weaver-profiles
    python tools/build_profile_index.py ../esp-weaver-profiles --validate
    python tools/build_profile_index.py ../esp-weaver-profiles --check
    python tools/build_profile_index.py pr \
        --pull 123 --repo acme/esp-weaver-profiles --commit <sha> \
        --output out/index.json

Without a flag the index is written. ``--check`` writes nothing and exits
non-zero if the index on disk is not what would be written; the profile
repository's CI runs it on ``master``. ``--validate`` writes nothing and
judges only the files, which is what CI does on a pull request. ``--pull``
builds the index of a pull request's files for the integration to read before
the merge (the repository's ``.github/workflows/profile_index.yml``): the
index names the pull request, the fork and the commit, so that the
integration locates each file by commit; when a file is refused, the index
carries the refusal instead of products, so the integration can say so, and
the exit code is still non-zero.

Espressif's own products are under ``vendors/espressif/`` like any vendor's;
the integration's built-in ``device_profiles/espressif/`` is a copy of them,
kept identical by ``tools/check_shipped_profiles.py``.
"""

import argparse
from pathlib import Path
import sys
import types

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _import_without_home_assistant() -> None:
    """Let the index modules import where Home Assistant is not installed.

    ``profiles.index`` and what it uses stay clear of Home Assistant, but the
    packages above them (``custom_components.esp_weaver`` and its
    ``profiles``) import it in their ``__init__``. Register those packages as
    bare namespaces so that their ``__init__`` is not run. Where they are
    already imported - the integration's own test suite - nothing changes.
    """
    path = REPO_ROOT
    for name in (
        "custom_components",
        "custom_components.esp_weaver",
        "custom_components.esp_weaver.profiles",
    ):
        path = path / name.rpartition(".")[2]
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules.setdefault(name, package)


_import_without_home_assistant()

from custom_components.esp_weaver.profiles.index import (  # noqa: E402
    INDEX_FILE,
    VENDORS_DIR,
    IndexSource,
    index_document,
    index_products,
    render_index,
)
from custom_components.esp_weaver.profiles.schema import (  # noqa: E402
    YAML_SUFFIXES,
    ProfileError,
)


def _profile_files(vendors: Path) -> list[Path]:
    """Return every ``vendors/<vendor>/<product>.yaml`` under ``vendors``."""
    files: list[Path] = []
    for vendor_dir in vendors.iterdir():
        if vendor_dir.name.startswith("."):
            continue  # .gitkeep, holding the directory while it is empty
        if not vendor_dir.is_dir():
            raise ProfileError(
                f"{vendor_dir.name}: expected {VENDORS_DIR}/<vendor>/<product>.yaml"
            )
        for path in vendor_dir.iterdir():
            if path.suffix in YAML_SUFFIXES and path.is_file():
                files.append(path)
            elif path.is_dir():
                raise ProfileError(
                    f"{vendor_dir.name}/{path.name}: expected "
                    f"{VENDORS_DIR}/<vendor>/<product>.yaml"
                )
    return files


def build_index(root: Path, source: IndexSource | None = None) -> dict:
    """Return the index for the repository at ``root``.

    Raises:
        ProfileError: A file is refused; see ``index_products`` for the rules,
            which are the integration's own.
    """
    files = [
        (path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
        for path in _profile_files(root / VENDORS_DIR)
    ]
    return index_document(index_products(files), source=source)


def _write(target: Path, text: str) -> None:
    """Write the index, making room for it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def main() -> int:
    """Build or check the index."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="repository root (holds vendors/)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate", action="store_true", help="judge the files only, write nothing"
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify the index is current, write nothing",
    )
    mode.add_argument(
        "--pull", type=int, help="build the index of this pull request's files"
    )
    parser.add_argument("--repo", help="with --pull: the fork, as owner/name")
    parser.add_argument("--commit", help="with --pull: the head commit, in full")
    parser.add_argument(
        "--output", type=Path, help="with --pull: where to write the index"
    )
    args = parser.parse_args()
    if not (args.root / VENDORS_DIR).is_dir():
        parser.error(f"{args.root} has no {VENDORS_DIR}/ directory")
    source = None
    if args.pull is not None:
        if not (args.repo and args.commit and args.output):
            parser.error("--pull needs --repo, --commit and --output")
        source = IndexSource(args.pull, args.repo, args.commit)
    elif args.repo or args.commit or args.output:
        parser.error("--repo, --commit and --output go with --pull")

    try:
        text = render_index(build_index(args.root, source))
    except ProfileError as err:
        print(f"error: {err}", file=sys.stderr)
        if source is not None:
            # The refusal travels with the index, for the integration to show.
            _write(
                args.output,
                render_index(index_document([], source=source, error=str(err))),
            )
            print(f"wrote the refusal to {args.output}")
        return 1

    if source is not None:
        _write(args.output, text)
        print(f"wrote {args.output} for #{source.pull} at {source.commit[:7]}")
        return 0

    if args.validate:
        print(f"{args.root / VENDORS_DIR}: every profile validates")
        return 0

    target = args.root / INDEX_FILE
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print(f"{target} is out of date; run without --check", file=sys.stderr)
            return 1
        print(f"{target} is up to date")
        return 0

    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
