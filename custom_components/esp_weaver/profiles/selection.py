# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Which product each device is bound to, and the registry that follows from it.

The binding is a config entry option: product id, source, and the hash of the
content chosen. The store holds that content, so the device keeps resolving
against what the user confirmed whatever happens to the file.
"""

from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir

from ..const import CONF_NODE_ID
from ..data import domain_data
from ..identity import DOMAIN
from .catalog import ProfileCatalog
from .loader import load_service_layer
from .product import ProductCandidate
from .registry import ProfileRegistry
from .schema import DeviceProfile, profile_blocks
from .store import async_get_store

_LOGGER = logging.getLogger(__name__)

# The config entry option, and its keys.
OPTION_PROFILE: Final = "profile"
BINDING_ID: Final = "id"
BINDING_SOURCE: Final = "source"
BINDING_HASH: Final = "hash"

# The update repair: one per product and source, for every device bound to it.
ISSUE_UPDATE_PREFIX: Final = "profile_update_"
# What the issue carries for its fix flow.
ISSUE_DATA_PRODUCT: Final = "product_id"
ISSUE_DATA_SOURCE: Final = "source"
ISSUE_DATA_HASH: Final = "hash"


class ProfileMissingError(ConfigEntryError):
    """The content a device is bound to cannot be produced.

    A ConfigEntryError: the entry fails outright rather than resolving against
    some other file. The way out is to remove the device and add it again.
    """

    def __init__(self, entry: ConfigEntry, binding: "ProfileBinding | None") -> None:
        """Describe the entry and what it was bound to, if anything."""
        if binding is None:
            key = "profile_unbound"
            placeholders = {"device": entry.title}
        else:
            key = "profile_missing"
            placeholders = {
                "device": entry.title,
                "product": binding.id,
                "hash": binding.hash,
            }
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders=placeholders,
        )
        self.binding = binding


@dataclass(frozen=True, slots=True)
class ProfileBinding:
    """What one config entry says about the product it resolves against."""

    id: str
    source: str
    hash: str

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> "ProfileBinding | None":
        """Read the binding off an entry, or None if it has no usable one."""
        option = entry.options.get(OPTION_PROFILE)
        if not isinstance(option, dict):
            return None
        try:
            return cls(
                id=str(option[BINDING_ID]),
                source=str(option[BINDING_SOURCE]),
                hash=str(option[BINDING_HASH]),
            )
        except KeyError:
            return None

    @classmethod
    def for_candidate(cls, candidate: ProductCandidate) -> "ProfileBinding":
        """Return the binding that choosing this candidate produces.

        For a kept copy, the source is the one it was kept from.
        """
        return cls(
            id=candidate.id, source=candidate.watched_source, hash=candidate.hash
        )

    def as_option(self) -> dict[str, Any]:
        """Return the binding in the form the config entry stores."""
        return {
            BINDING_ID: self.id,
            BINDING_SOURCE: self.source,
            BINDING_HASH: self.hash,
        }


def options_with_binding(
    options: dict[str, Any] | None, binding: ProfileBinding
) -> dict[str, Any]:
    """Return the entry options with this binding in place of any earlier one."""
    return {**(options or {}), OPTION_PROFILE: binding.as_option()}


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """What the device page says a device is, from the product it is bound to.

    The serial number is the node id. Manufacturer and model are the bound
    product's vendor and label: what Home Assistant treats the device as,
    which its entities are built from. They follow the binding, so a rebind
    changes them. The model id is not here: it is the device's own word
    (``info.model``, when the firmware set one), written by the coordinator
    from the node config, so the page shows both what the user picked and
    what the device says it is.
    """

    serial_number: str
    manufacturer: str | None = None
    model: str | None = None

    @classmethod
    def of(
        cls,
        node_id: str,
        product: ProductCandidate | None,
        language: str = "en",
    ) -> "DeviceIdentity":
        """Return what the page says about a node bound to this product."""
        if product is None:
            return cls(node_id)
        return cls(
            serial_number=node_id,
            manufacturer=product.localized_vendor(language),
            model=product.label,
        )


def update_issue_id(product_id: str, source: str) -> str:
    """Return the id of the repair offering this product's content from this source."""
    return f"{ISSUE_UPDATE_PREFIX}{source}__{product_id.replace('/', '__')}"


def entries_to_update(
    hass: HomeAssistant, product_id: str, source: str, content_hash: str
) -> list[ConfigEntry]:
    """Return the entries bound to this product from this source and behind it."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (binding := ProfileBinding.from_entry(entry)) is not None
        and binding.id == product_id
        and binding.source == source
        and binding.hash != content_hash
    ]


async def _async_service_layer(hass: HomeAssistant) -> list[DeviceProfile]:
    """Return the built-in service layer, read once per run."""
    data = domain_data(hass)
    if data.service_layer is None:
        data.service_layer = await hass.async_add_executor_job(load_service_layer)
    return data.service_layer


async def async_registry_for_candidate(
    hass: HomeAssistant, candidate: ProductCandidate
) -> ProfileRegistry:
    """Return the registry a device bound to this candidate resolves against.

    Raises:
        ValueError: The candidate has not been fetched.
    """
    if candidate.content is None:
        raise ValueError(f"{candidate.id} has no content to build a registry from")

    # Keyed by product as well as hash: the registry is stamped with the
    # product id it was built for, and two products can share a mapping.
    registries = domain_data(hass).registries
    cache_key = (candidate.id, candidate.hash)
    if (cached := registries.get(cache_key)) is not None:
        return cached

    devices, services = profile_blocks(candidate.content, candidate.id)
    registry = ProfileRegistry.for_product(
        candidate.id, devices, services, await _async_service_layer(hass)
    )
    registries[cache_key] = registry
    return registry


async def async_stored_product_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> ProductCandidate:
    """Return the product an entry is bound to, exactly as the store holds it.

    No other file is substituted; what the user confirmed is what runs.

    Raises:
        ProfileMissingError: The entry has no binding, or the store no longer
            holds the content it is bound to.
    """
    binding = ProfileBinding.from_entry(entry)
    if binding is None:
        _LOGGER.error(
            "Node %s was added before products were chosen per device and has "
            "no binding; remove it and add it again",
            entry.data.get(CONF_NODE_ID),
        )
        raise ProfileMissingError(entry, None)

    store = await async_get_store(hass)
    stored = store.get(binding.id, binding.hash)
    if stored is None:
        _LOGGER.error(
            "Node %s is bound to %s at %s, which is no longer stored; remove "
            "the device and add it again",
            entry.data.get(CONF_NODE_ID),
            binding.id,
            binding.hash,
        )
        raise ProfileMissingError(entry, binding)
    return stored


async def async_registry_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> ProfileRegistry:
    """Return the registry this entry's device resolves against.

    Raises:
        ProfileMissingError: The entry has no binding, or the store no longer
            holds the content it is bound to.
    """
    stored = await async_stored_product_for_entry(hass, entry)
    return await async_registry_for_candidate(hass, stored)


async def async_keep(
    hass: HomeAssistant, candidate: ProductCandidate
) -> ProfileBinding:
    """Keep a candidate's content and return the binding that names it.

    The store is always written before the binding exists.
    """
    store = await async_get_store(hass)
    await store.async_put(candidate)
    return ProfileBinding.for_candidate(candidate)


async def async_bind(
    hass: HomeAssistant, entry: ConfigEntry, candidate: ProductCandidate
) -> ProfileBinding:
    """Bind an entry to a candidate, keeping the content it was bound to."""
    binding = await async_keep(hass, candidate)
    hass.config_entries.async_update_entry(
        entry, options=options_with_binding(dict(entry.options), binding)
    )
    return binding


@callback
def async_sync_binding_issues(hass: HomeAssistant, catalog: ProfileCatalog) -> None:
    """Raise or withdraw the update repairs, against the current catalog.

    One repair per product whose current content differs from what its devices
    are bound to. Only the source a device was bound from is watched. Nothing
    here changes a binding; the repair's fix flow does.
    """
    issues = ir.async_get(hass)

    outdated: dict[tuple[str, str], ProductCandidate] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        binding = ProfileBinding.from_entry(entry)
        if binding is None:
            continue
        copies = [c for c in catalog.by_id(binding.id) if c.source == binding.source]
        if copies and all(c.hash != binding.hash for c in copies):
            outdated[(binding.id, binding.source)] = copies[0]

    wanted = {
        update_issue_id(product_id, source): candidate
        for (product_id, source), candidate in outdated.items()
    }
    for domain, issue_id in list(issues.issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(ISSUE_UPDATE_PREFIX)
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    for issue_id, candidate in wanted.items():
        devices = entries_to_update(
            hass, candidate.id, candidate.source, candidate.hash
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="profile_update",
            translation_placeholders={
                "product": candidate.id,
                "product_label": candidate.label,
                "source": candidate.source,
                "count": str(len(devices)),
                "devices": ", ".join(entry.title for entry in devices),
            },
            data={
                ISSUE_DATA_PRODUCT: candidate.id,
                ISSUE_DATA_SOURCE: candidate.source,
                ISSUE_DATA_HASH: candidate.hash,
            },
        )


async def async_prune_store(hass: HomeAssistant) -> None:
    """Drop stored content no entry is bound to."""
    keep = {
        (binding.id, binding.hash)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (binding := ProfileBinding.from_entry(entry)) is not None
    }
    store = await async_get_store(hass)
    await store.async_prune(keep)
