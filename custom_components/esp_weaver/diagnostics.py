# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Diagnostics support for ESP-Weaver integration."""

import logging
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_CUSTOM_POP, CONF_NODE_ID
from .helpers.ha_types import ESPConfigEntry
from .iot.specs.keys import (
    KEY_HW_VERSION,
    KEY_IDENTIFIERS,
    KEY_IP,
    KEY_MAC,
    KEY_MANUFACTURER,
    KEY_MODEL,
    KEY_NAME,
    KEY_POP,
    KEY_SW_VERSION,
)
from .profiles import (
    CUSTOM_PROFILE_DIR,
    ProfileBinding,
    ProfileMissingError,
    async_get_catalog,
    async_registry_for_entry,
    build_preview,
)

if TYPE_CHECKING:
    from .iot.client.device_api import ESPWeaverApi

_LOGGER = logging.getLogger(__name__)

# What a removed value is replaced with, whole or in part.
REDACTED: Final = "**REDACTED**"
REDACTED_MAC: Final = "REDACTED_MAC"

# A MAC address as either notation writes it: xx:xx:xx:xx:xx:xx or xx-xx-...
_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")

# Keys whose value never leaves the device. Matched as substrings, so each
# entry covers the compound names firmware and the cloud spell it with.
SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        CONF_CUSTOM_POP,
        CONF_PASSWORD,
        KEY_POP,
        KEY_MAC,
        "token",
        "secret",
        "api_key",
        "serial",
    }
)
# Keys holding an address, kept legible to its last octet. Matched whole.
ADDRESS_KEYS: Final[frozenset[str]] = frozenset({CONF_HOST, KEY_IP, "ip_address"})


def _redact_identifier(identifier: tuple[Any, ...]) -> list[Any]:
    """Return one device identifier with any MAC address in it taken out."""
    return [
        REDACTED_MAC if isinstance(item, str) and _MAC_PATTERN.match(item) else item
        for item in identifier
    ]


def _redact_ip_address(ip: str) -> str:
    """Return an IPv4 address as its last octet only, e.g. ``***.***.***.100``.

    Anything that is not an IPv4 address is removed outright.
    """
    if ip and "." in ip:
        parts = ip.split(".")
        if len(parts) == 4 and all(
            part.isdigit() and 0 <= int(part) <= 255 for part in parts
        ):
            return f"***.***.***.{parts[3]}"

    return REDACTED


def redact(value: Any) -> Any:
    """Return a copy of a structure with the values of secret keys taken out.

    Secret keys match as substrings (``node_mac``), erring towards
    over-redaction; address keys match whole and keep their last octet, which
    a bug report needs. Only keys decide, never values. This is why Home
    Assistant's ``async_redact_data`` - whole keys, values blanked - is not used.
    """
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, dict):
        return value

    result: dict[str, Any] = {}
    for key, item in value.items():
        lowered = key.lower()
        if any(secret in lowered for secret in SECRET_KEYS):
            result[key] = REDACTED
        elif lowered in ADDRESS_KEYS:
            result[key] = (
                _redact_ip_address(item) if isinstance(item, str) else REDACTED
            )
        else:
            result[key] = redact(item)
    return result


def _get_redacted_device_data(api: "ESPWeaverApi", node_id: str) -> dict[str, Any]:
    """Return everything the API layer holds about one node, redacted."""
    if not (device_data := api.get_device_data(node_id)):
        return {}
    return {"device_data": redact(device_data)}


async def _get_profile_preview(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: "ESPWeaverApi",
    node_id: str,
) -> dict[str, Any]:
    """Rerun the resolution the entities were built from, so a bug report shows it."""
    node_config, params = api.get_reported_config(node_id)
    if not node_config:
        return {"profiles_error": "Device has not reported a config yet"}

    existing = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    try:
        registry = await async_registry_for_entry(hass, entry)
        preview = build_preview(
            registry,
            node_id,
            node_config,
            params,
            existing_unique_ids=existing,
        )
    except (ProfileMissingError, KeyError, TypeError, ValueError) as err:
        # A report is more use with the failure in it than without one at all.
        _LOGGER.exception("Failed to build the profile preview for %s", node_id)
        return {"profiles_error": f"{type(err).__name__}: {err}"}

    return {"profiles": preview}


async def _get_profile_files(
    hass: HomeAssistant, entry: ESPConfigEntry
) -> dict[str, Any]:
    """Report which product this device is bound to, and what could be offered.

    The profile folder is named relative to the configuration directory.
    """
    catalog = await async_get_catalog(hass)
    binding = ProfileBinding.from_entry(entry)
    return {
        "profile_files": {
            "custom_dir": CUSTOM_PROFILE_DIR,
            "binding": None
            if binding is None
            else {
                "id": binding.id,
                "source": binding.source,
                "hash": binding.hash,
            },
            **catalog.describe(),
        }
    }


async def _async_node_details(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: "ESPWeaverApi",
    node_id: str,
) -> dict[str, Any]:
    """Return the sections about the node itself, shared by both reports."""
    return {
        **_get_redacted_device_data(api, node_id),
        **await _get_profile_preview(hass, entry, api, node_id),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    node_id = entry.data[CONF_NODE_ID]

    diagnostics_data: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "domain": entry.domain,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": redact(dict(entry.data)),
        },
        "coordinator": {"is_available": coordinator.is_available},
    }
    diagnostics_data.update(await _get_profile_files(hass, entry))

    api = coordinator.api
    diagnostics_data["api_status"] = {
        "device_registered": api.get_device(node_id) is not None,
        "device_available": await api.is_device_available_async(node_id),
        "discovery_completed": api.is_discovery_completed(node_id),
    }
    diagnostics_data.update(await _async_node_details(hass, entry, api, node_id))

    return diagnostics_data


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for one device, as downloaded from its page."""
    coordinator = entry.runtime_data
    node_id = entry.data[CONF_NODE_ID]

    diagnostics_data: dict[str, Any] = {
        "device": {
            KEY_NAME: device.name,
            KEY_MODEL: device.model,
            KEY_MANUFACTURER: device.manufacturer,
            KEY_SW_VERSION: device.sw_version,
            KEY_HW_VERSION: device.hw_version,
            KEY_IDENTIFIERS: [_redact_identifier(i) for i in device.identifiers],
        },
        "entry": {
            "title": entry.title,
            CONF_NODE_ID: node_id,
        },
    }

    api = coordinator.api
    diagnostics_data["connectivity"] = {
        "available": await api.is_device_available_async(node_id),
        "coordinator_available": coordinator.is_available,
    }
    diagnostics_data.update(await _async_node_details(hass, entry, api, node_id))

    return diagnostics_data
