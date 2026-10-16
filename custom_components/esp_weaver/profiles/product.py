# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""One product profile as something that can be offered, chosen and bound.

A product is one YAML document under a vendor and product name. Its source -
shipped, configuration directory, kept from an earlier binding, repository
index - decides only what has to happen before it can be used. Every file found
is offered; two claiming the same product are two entries.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .schema import ParsedProfile

# Where a candidate was found, in the order equal products are listed.
SOURCE_LOCAL: Final = "local"
SOURCE_BUILTIN: Final = "builtin"
SOURCE_CACHED: Final = "cached"
SOURCE_REPO: Final = "repo"
SOURCE_ORDER: Final[tuple[str, ...]] = (
    SOURCE_LOCAL,
    SOURCE_BUILTIN,
    SOURCE_CACHED,
    SOURCE_REPO,
)


def product_id(vendor: str, product: str) -> str:
    """Return the id a vendor and product pair is known by everywhere."""
    return f"{vendor}/{product}"


# What kind of thing a product is, for grouping a vendor's list; read off the
# platforms it declares. In listing order.
CATEGORY_LIGHTING: Final = "lighting"
CATEGORY_CLIMATE: Final = "climate"
CATEGORY_COVERS: Final = "covers"
CATEGORY_SECURITY: Final = "security"
CATEGORY_MEDIA: Final = "media"
CATEGORY_CLEANING: Final = "cleaning"
CATEGORY_SWITCHES: Final = "switches"
CATEGORY_SENSORS: Final = "sensors"
CATEGORY_MULTI: Final = "multi"
CATEGORY_OTHER: Final = "other"
# A repository entry whose index does not say; listed last, without a group.
CATEGORY_UNKNOWN: Final = ""

# Checked in this order: a garage door with a lock is a cover, a humidifier
# with a power switch is climate.
_PLATFORM_CATEGORIES: Final[tuple[tuple[frozenset[str], str], ...]] = (
    (frozenset({"light"}), CATEGORY_LIGHTING),
    (frozenset({"climate", "fan", "humidifier", "water_heater"}), CATEGORY_CLIMATE),
    (frozenset({"cover", "valve"}), CATEGORY_COVERS),
    (frozenset({"lock", "alarm_control_panel", "siren"}), CATEGORY_SECURITY),
    (frozenset({"media_player", "remote"}), CATEGORY_MEDIA),
    (frozenset({"vacuum", "lawn_mower"}), CATEGORY_CLEANING),
    (frozenset({"switch"}), CATEGORY_SWITCHES),
    (frozenset({"sensor", "binary_sensor", "event"}), CATEGORY_SENSORS),
)
CATEGORY_ORDER: Final[tuple[str, ...]] = (
    *(category for _, category in _PLATFORM_CATEGORIES),
    CATEGORY_MULTI,
    CATEGORY_OTHER,
    CATEGORY_UNKNOWN,
)


def category_of(validated: dict[str, Any]) -> str:
    """Return the group a validated product document belongs in.

    Several device types is multi-device; one type is grouped by its
    platforms, else "other".
    """
    devices = validated.get("devices") or []
    if len(devices) > 1:
        return CATEGORY_MULTI
    platforms = {
        entity["platform"] for device in devices for entity in device["entities"]
    }
    for members, category in _PLATFORM_CATEGORIES:
        if platforms & members:
            return category
    return CATEGORY_OTHER


def category_rank(category: str) -> int:
    """Return where a category sits in the list's grouping."""
    return (
        CATEGORY_ORDER.index(category)
        if category in CATEGORY_ORDER
        else len(CATEGORY_ORDER)
    )


@dataclass(frozen=True, slots=True)
class ProfileProblem:
    """One profile file that could not be offered; kept for repairs and diagnostics."""

    kind: str
    path: Path
    detail: str = ""

    @property
    def filename(self) -> str:
        """Return the file's name, which is what the user recognises."""
        return self.path.name

    @property
    def issue_name(self) -> str:
        """Return what tells this file apart in a repair id."""
        return f"{self.path.parent.name}_{self.path.name}"


def _localized(labels: dict[str, str], language: str, default: str) -> str:
    """Return labels' name for a language, its base language, or the default.

    Home Assistant reports regional tags (en-GB, es-419); a translation
    written for the base language (en, es) serves those users too, so the
    base language is tried before the default.
    """
    if (name := labels.get(language)) is not None:
        return name
    if (name := labels.get(language.partition("-")[0])) is not None:
        return name
    return default


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    """One product a device could be bound to."""

    vendor: str
    product: str
    label: str
    source: str
    hash: str
    # The model the file declares, which its devices report (fit.py); empty
    # for the service layer, which has none.
    model: str = ""
    # Path relative to its directory or in the repository; empty for a cached
    # copy. Shown when two candidates share an id.
    location: str = ""
    # The validated document. None for a repository entry that has only been
    # listed so far; fetching it fills this in.
    content: dict[str, Any] | None = None
    url: str | None = None
    path: Path | None = None
    # Which group of a vendor's list this sits in; see ``category_of``. Read
    # from the content when there is content, from the index otherwise.
    category: str = CATEGORY_UNKNOWN
    # For a kept copy, the source it was kept from, which its bindings watch.
    origin: str = ""
    # Optional localized forms of ``label``, keyed by Home Assistant language.
    labels: dict[str, str] = field(default_factory=dict)
    # Display forms of the vendor id; the id itself remains a stable slug.
    vendor_label: str = ""
    vendor_labels: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Return the product id."""
        return product_id(self.vendor, self.product)

    @property
    def title(self) -> str:
        """Return the product as a form names it: vendor, then product."""
        return f"{self.vendor} › {self.name}"

    def localized_label(self, language: str) -> str:
        """Return the label for a Home Assistant language, or the default."""
        return _localized(self.labels, language, self.label)

    def localized_vendor(self, language: str) -> str:
        """Return the vendor name for a Home Assistant language."""
        return _localized(
            self.vendor_labels, language, self.vendor_label or self.vendor
        )

    def localized_name(self, language: str) -> str:
        """Return the localized label followed by the unchanged model."""
        label = self.localized_label(language)
        return f"{label}-{self.model}" if self.model else label

    def localized_title(self, language: str) -> str:
        """Return the vendor and localized product name."""
        return f"{self.localized_vendor(language)} › {self.localized_name(language)}"

    @property
    def watched_source(self) -> str:
        """Return the source a binding to this candidate is told about updates from."""
        return self.origin or self.source

    @property
    def key(self) -> str:
        """Return what tells this candidate apart from every other in the list.

        Id, source and location; id and hash for a cached copy.
        """
        return f"{self.source}:{self.location or f'{self.id}@{self.hash}'}"

    @property
    def rank(self) -> int:
        """Return where this candidate's source sits in the list's grouping."""
        return SOURCE_ORDER.index(self.source)

    @property
    def category_rank(self) -> int:
        """Return where this candidate's category sits in the list's grouping."""
        return category_rank(self.category)

    @classmethod
    def from_parsed(
        cls,
        parsed: ParsedProfile,
        *,
        source: str,
        location: str = "",
        path: Path | None = None,
        url: str | None = None,
    ) -> "ProductCandidate":
        """Build a candidate from a profile that validated, hashed as written."""
        validated = parsed.validated
        vendor: str = validated["vendor"]
        product: str = validated["product"]
        return cls(
            vendor=vendor,
            product=product,
            label=validated.get("product_label") or product_id(vendor, product),
            labels=dict(validated.get("product_labels") or {}),
            vendor_label=validated.get("vendor_label") or vendor,
            vendor_labels=dict(validated.get("vendor_labels") or {}),
            model=validated.get("model", ""),
            source=source,
            hash=parsed.hash,
            location=location,
            content=validated,
            path=path,
            url=url,
            category=category_of(validated),
        )

    @property
    def name(self) -> str:
        """Return the product as the user reads it: "Light-A10", or "Light".

        A dash between label and model, so a label of several words still
        reads as a name and a model; the device list joins them the same way.
        """
        return f"{self.label}-{self.model}" if self.model else self.label

    def describe(self) -> dict[str, Any]:
        """Return the candidate without its content, for diagnostics."""
        return {
            "id": self.id,
            "product_label": self.label,
            "product_labels": self.labels,
            "vendor_label": self.vendor_label,
            "vendor_labels": self.vendor_labels,
            "model": self.model,
            "source": self.source,
            "location": self.location,
            "category": self.category,
            "hash": self.hash,
            "path": str(self.path) if self.path else None,
            "url": self.url,
        }
