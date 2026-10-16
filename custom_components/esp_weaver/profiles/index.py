# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""What the repository's ``index.json`` says about its files, computed from them.

The profile repository (``espressif/esp-weaver-profiles``) is
``vendors/<vendor>/<product>.yaml`` and nothing else the integration reads.
``tools/build_profile_index.py`` runs this over the files on disk and writes
the result: the published index on ``master``, and an index per pull request
that the repository's CI publishes for the integration to read before the
merge (``github.py``). Both are read back here, so what the tool wrote is
what the integration lists.

This module and what it imports (``schema``, ``product``) stay clear of Home
Assistant, so the tool runs where only PyYAML and voluptuous are installed:
the profile repository's CI, a vendor's machine.
"""

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from typing import Any, Final

import voluptuous as vol

from .product import CATEGORY_UNKNOWN, SOURCE_REPO, ProductCandidate, product_id
from .schema import HASH_LENGTH, SLUG, YAML_SUFFIXES, ProfileError, parse_profile

VENDORS_DIR: Final = "vendors"
INDEX_FILE: Final = "index.json"
INDEX_VERSION: Final = 1

GITHUB_RAW: Final = "https://raw.githubusercontent.com"
# An index lists every product in the repository in a few hundred bytes each;
# it has no business being larger.
MAX_INDEX_SIZE: Final = 2 * 1024 * 1024

_REPO: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+")
_SHA: Final = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class IndexSource:
    """The pull request an index was built from: which, of what, at which commit.

    The published index has none; an index CI builds for a pull request says
    so, and where its files are: ``repo`` is the fork the pull request was
    opened from, ``commit`` its head when the index was built.
    """

    pull: int
    repo: str
    commit: str

    @property
    def raw_base(self) -> str:
        """Return where the indexed files are read from, by commit.

        Files by commit never change, so a product downloaded later is the
        one the index hashed, however the pull request has moved since.
        """
        return f"{GITHUB_RAW}/{self.repo}/{self.commit}/"

    def as_document(self) -> dict[str, Any]:
        """Return the source as it is written into the index."""
        return {"pull": self.pull, "repo": self.repo, "commit": self.commit}


@dataclass(frozen=True, slots=True)
class ParsedIndex:
    """An index read back: its products, and what it says about itself."""

    products: list[ProductCandidate]
    source: IndexSource | None
    # CI's refusal of the pull request's files, in place of products.
    error: str | None


INDEX_ENTRY_SCHEMA: Final = vol.Schema(
    {
        vol.Required("vendor"): SLUG,
        vol.Required("product"): SLUG,
        vol.Required("path"): vol.All(str, vol.Length(min=1)),
        vol.Required("hash"): vol.All(
            str, vol.Length(min=HASH_LENGTH, max=HASH_LENGTH)
        ),
        vol.Optional("product_label"): str,
        vol.Optional("product_labels"): {str: str},
        vol.Optional("vendor_label"): str,
        vol.Optional("vendor_labels"): {str: str},
        vol.Optional("model"): str,
        vol.Optional("category"): str,
    },
    extra=vol.ALLOW_EXTRA,
)
INDEX_SOURCE_SCHEMA: Final = vol.Schema(
    {
        vol.Required("pull"): vol.All(int, vol.Range(min=1)),
        vol.Required("repo"): vol.Match(_REPO),
        vol.Required("commit"): vol.Match(_SHA),
    },
    extra=vol.ALLOW_EXTRA,
)
INDEX_SCHEMA: Final = vol.Schema(
    {
        vol.Required("version"): vol.In({INDEX_VERSION}),
        vol.Required("products"): [INDEX_ENTRY_SCHEMA],
        vol.Optional("source"): INDEX_SOURCE_SCHEMA,
        vol.Optional("error"): str,
    },
    extra=vol.ALLOW_EXTRA,
)


def is_profile_path(path: str) -> bool:
    """Say whether a path, relative to the repository root, is a product file.

    ``vendors/<vendor>/<product>.yaml``, and not a dotfile: a leading dot is
    ``.gitkeep`` holding an empty directory, or an editor's backup.
    """
    parts = PurePosixPath(path).parts
    return (
        len(parts) == 3
        and parts[0] == VENDORS_DIR
        and not parts[1].startswith(".")
        and not parts[2].startswith(".")
        and PurePosixPath(parts[2]).suffix in YAML_SUFFIXES
    )


def _shown(text: str) -> str:
    """Return the text as a browser shows it: trimmed, runs of blanks as one."""
    return " ".join(text.split())


def _check_edges(path: str, key: str, value: str) -> None:
    """Reject a ``label`` or ``model`` with blanks at either end.

    Invisible in the file and in the list, but ``model`` is compared with the
    firmware's byte for byte, so ``"A2 "`` fits no device and says nothing.
    """
    if value != value.strip():
        raise ProfileError(f"{path}: {key} {value!r} starts or ends with whitespace")


def index_products(files: Iterable[tuple[str, str]]) -> list[ProductCandidate]:
    """Return one candidate per file, in path order, each with its content.

    Args:
        files: Each file's path relative to the repository root, posix style,
            and its text.

    Raises:
        ProfileError: A path is not ``vendors/<vendor>/<product>.yaml``; a file
            does not validate; its ``vendor``/``product`` disagree with the
            directory it sits in (the path is the identity the repository is
            organised by, so the two have to agree); its ``label`` or ``model``
            has whitespace at an end; or two of one vendor's files show the
            same name in the list. The list is where the user tells products
            apart, so a vendor's names have to differ.
    """
    products: list[ProductCandidate] = []
    # Where each name a vendor's list would show came from, to name both
    # files when a second one shows the same.
    shown: dict[tuple[str, str], str] = {}
    for path, text in sorted(files):
        if not is_profile_path(path):
            raise ProfileError(
                f"{path}: expected {VENDORS_DIR}/<vendor>/<product>.yaml"
            )
        parts = PurePosixPath(path)
        vendor, product = parts.parts[1], parts.stem

        parsed = parse_profile(text, path)
        candidate = ProductCandidate.from_parsed(
            parsed, source=SOURCE_REPO, location=path
        )
        if (candidate.vendor, candidate.product) != (vendor, product):
            raise ProfileError(
                f"{path}: declares {candidate.id} but sits under {vendor}/{product}"
            )
        _check_edges(path, "product_label", candidate.label)
        for language, label in candidate.labels.items():
            _check_edges(path, f"product_labels.{language}", label)
        _check_edges(path, "vendor_label", candidate.vendor_label)
        for language, label in candidate.vendor_labels.items():
            _check_edges(path, f"vendor_labels.{language}", label)
        _check_edges(path, "model", candidate.model)
        name = (vendor, _shown(candidate.name))
        if (other := shown.get(name)) is not None:
            raise ProfileError(
                f"{path} and {other} both show as {name[1]!r} in {vendor}'s list; "
                "change the label or model of one"
            )
        shown[name] = path
        products.append(candidate)

    ids = [candidate.id for candidate in products]
    if len(ids) != len(set(ids)):
        raise ProfileError("two files map to the same product id")
    return products


def index_document(
    products: Iterable[ProductCandidate],
    *,
    source: IndexSource | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Return the index as a document: what the integration downloads.

    An index built for a pull request names its ``source``; one whose files
    were refused carries the refusal as ``error`` in place of products, so
    that the integration can say why the pull request lists nothing.
    """
    document: dict[str, Any] = {"version": INDEX_VERSION}
    if source is not None:
        document["source"] = source.as_document()
    if error is not None:
        document["error"] = error
    document["products"] = [
        {
            "vendor": candidate.vendor,
            "product": candidate.product,
            "product_label": candidate.label,
            **({"product_labels": candidate.labels} if candidate.labels else {}),
            **(
                {"vendor_label": candidate.vendor_label}
                if candidate.vendor_label != candidate.vendor
                else {}
            ),
            **(
                {"vendor_labels": candidate.vendor_labels}
                if candidate.vendor_labels
                else {}
            ),
            "model": candidate.model,
            "category": candidate.category,
            "path": candidate.location,
            "hash": candidate.hash,
        }
        for candidate in products
    ]
    return document


def render_index(document: dict[str, Any]) -> str:
    """Return the index as it is written: stable, diff-friendly JSON."""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def parse_index(text: str, base: str | None) -> ParsedIndex:
    """Read an index back into candidates that know where their file is.

    Args:
        text: The index document.
        base: Where the files are, for an index that does not say - the
            published index, read from one of ``PROFILE_SOURCES``. An index
            with a ``source`` locates its files by that commit instead.

    Raises:
        ProfileError: The document is not an index this version reads, or
            says nothing about where its files are.
    """
    try:
        index = INDEX_SCHEMA(json.loads(text))
    except (json.JSONDecodeError, vol.Invalid) as err:
        raise ProfileError(f"is not usable as an index: {err}") from err

    source = None
    if (said := index.get("source")) is not None:
        source = IndexSource(said["pull"], said["repo"], said["commit"])
        base = source.raw_base
    if base is None:
        raise ProfileError("index does not say where its files are")

    candidates: list[ProductCandidate] = []
    for entry in index["products"]:
        path = entry["path"].lstrip("/")
        candidates.append(
            ProductCandidate(
                vendor=entry["vendor"],
                product=entry["product"],
                label=entry.get("product_label")
                or product_id(entry["vendor"], entry["product"]),
                labels=dict(entry.get("product_labels") or {}),
                vendor_label=entry.get("vendor_label") or entry["vendor"],
                vendor_labels=dict(entry.get("vendor_labels") or {}),
                model=entry.get("model", ""),
                source=SOURCE_REPO,
                hash=entry["hash"],
                location=path,
                url=base + path,
                category=entry.get("category", CATEGORY_UNKNOWN),
            )
        )
    return ParsedIndex(candidates, source, index.get("error"))
