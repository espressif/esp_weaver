# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Keeps the exact profile each device was bound to, by product and content.

A device binds to a product id and a content hash, not to a file: editing or
deleting the file changes nothing until the user says so. Records are keyed by
product id and hash together, since two products can share a mapping (and so a
hash) while differing in label and manufacturer.
"""

from datetime import UTC, datetime, timedelta
import logging
from typing import Any, Final, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..data import domain_data
from ..identity import DOMAIN
from .product import SOURCE_CACHED, ProductCandidate, category_of
from .schema import ProfileError, validate_profile

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY: Final = f"{DOMAIN}.profiles"
STORAGE_VERSION: Final = 1

KEY_PRODUCTS: Final = "products"
KEY_ID: Final = "id"
KEY_VENDOR: Final = "vendor"
KEY_PRODUCT: Final = "product"
KEY_PRODUCT_LABEL: Final = "product_label"
KEY_PRODUCT_LABELS: Final = "product_labels"
KEY_VENDOR_LABEL: Final = "vendor_label"
KEY_VENDOR_LABELS: Final = "vendor_labels"
KEY_CONTENT: Final = "content"
# Where the content was chosen from, so that a binding made from this copy
# later is told about updates to that source rather than to no source.
KEY_SOURCE: Final = "source"
KEY_STORED_AT: Final = "stored_at"

_KEY_SEPARATOR: Final = "@"

# A record younger than this is never pruned: content is stored a moment
# before the entry pointing at it exists.
PRUNE_GRACE: Final = timedelta(minutes=5)


def record_key(product_id: str, content_hash: str) -> str:
    """Return the key one product's content is stored under."""
    return f"{product_id}{_KEY_SEPARATOR}{content_hash}"


class ProfileStore:
    """The profiles devices are bound to, persisted under ``.storage``."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Wrap Home Assistant's store without reading it yet."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._products: dict[str, dict[str, Any]] = {}
        # Records the current schema refuses, kept so a save does not lose them.
        self._rejected: dict[str, dict[str, Any]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Read the store, once, and revalidate every record against today's schema.

        The schema moves between releases. A record that no longer passes is
        not offered, with a WARNING; a device bound to it then fails to set up
        until the user chooses the product again from its options flow. The
        record itself is kept on disk - a later release may read it.
        """
        if self._loaded:
            return
        data = await self._store.async_load()
        products = (data or {}).get(KEY_PRODUCTS)
        stored = products if isinstance(products, dict) else {}
        self._products = {}
        self._rejected = {}
        for key, record in stored.items():
            content = record.get(KEY_CONTENT) if isinstance(record, dict) else None
            try:
                validated = validate_profile(content, f"stored profile {key}")
            except ProfileError as err:
                _LOGGER.warning(
                    "Stored profile %s no longer passes the profile schema and is "
                    "not offered; choose the product again for any device bound "
                    "to it: %s",
                    key,
                    err,
                )
                self._rejected[key] = record
                continue
            self._products[key] = {**record, KEY_CONTENT: validated}
        self._loaded = True

    def get(self, product_id: str, content_hash: str) -> ProductCandidate | None:
        """Return the stored profile for this product and content, or None."""
        key = record_key(product_id, content_hash)
        record = self._products.get(key)
        if record is None:
            return None
        return self._candidate(content_hash, record)

    def candidates(self) -> list[ProductCandidate]:
        """Return everything stored, each as a candidate a device could choose."""
        return [
            self._candidate(key.rsplit(_KEY_SEPARATOR, 1)[-1], record)
            for key, record in self._products.items()
        ]

    async def async_put(self, candidate: ProductCandidate) -> None:
        """Keep a candidate's content by product id and hash; existing keys stay."""
        if candidate.content is None:
            raise ValueError(f"{candidate.id} has no content to store")
        key = record_key(candidate.id, candidate.hash)
        if key in self._products:
            return
        self._products[key] = {
            KEY_ID: candidate.id,
            KEY_VENDOR: candidate.vendor,
            KEY_PRODUCT: candidate.product,
            KEY_PRODUCT_LABEL: candidate.label,
            KEY_PRODUCT_LABELS: candidate.labels,
            KEY_VENDOR_LABEL: candidate.vendor_label,
            KEY_VENDOR_LABELS: candidate.vendor_labels,
            KEY_CONTENT: candidate.content,
            KEY_SOURCE: candidate.watched_source,
            KEY_STORED_AT: datetime.now(UTC).isoformat(),
        }
        await self._save()
        _LOGGER.debug("Stored profile %s as %s", candidate.id, candidate.hash)

    async def async_prune(self, keep: set[tuple[str, str]]) -> None:
        """Drop every stored profile not among the ``(product id, hash)`` pairs given.

        Content stored within ``PRUNE_GRACE`` is kept regardless.
        """
        wanted = {record_key(product_id, hash_) for product_id, hash_ in keep}
        cutoff = datetime.now(UTC) - PRUNE_GRACE
        stale = [
            key
            for key, record in self._products.items()
            if key not in wanted and _stored_before(record, cutoff)
        ]
        if not stale:
            return
        for key in stale:
            del self._products[key]
        await self._save()
        _LOGGER.debug("Pruned %d unreferenced profiles from the store", len(stale))

    async def _save(self) -> None:
        """Write a snapshot, keeping the records the schema refused as they were."""
        await self._store.async_save(
            {
                KEY_PRODUCTS: {
                    key: dict(record)
                    for key, record in (
                        *self._rejected.items(),
                        *self._products.items(),
                    )
                }
            }
        )

    @staticmethod
    def _candidate(content_hash: str, record: dict[str, Any]) -> ProductCandidate:
        """Rebuild a candidate from a stored record."""
        return ProductCandidate(
            vendor=record[KEY_VENDOR],
            product=record[KEY_PRODUCT],
            label=record.get(KEY_PRODUCT_LABEL) or record[KEY_ID],
            labels=dict(record.get(KEY_PRODUCT_LABELS) or {}),
            vendor_label=record.get(KEY_VENDOR_LABEL) or record[KEY_VENDOR],
            vendor_labels=dict(record.get(KEY_VENDOR_LABELS) or {}),
            model=str(record[KEY_CONTENT].get("model", "")),
            source=SOURCE_CACHED,
            hash=content_hash,
            content=cast(dict[str, Any], record[KEY_CONTENT]),
            category=category_of(record[KEY_CONTENT]),
            origin=record.get(KEY_SOURCE, ""),
        )


def _stored_before(record: dict[str, Any], cutoff: datetime) -> bool:
    """Say whether a record is old enough to prune; one with no date is."""
    try:
        return datetime.fromisoformat(record[KEY_STORED_AT]) < cutoff
    except (KeyError, TypeError, ValueError):
        return True


async def async_get_store(hass: HomeAssistant) -> ProfileStore:
    """Return the shared store, reading it on first use."""
    data = domain_data(hass)
    if data.store is None:
        data.store = ProfileStore(hass)
    await data.store.async_load()
    return data.store
