# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Diagnostics support for ESP-Weaver integration.

This module provides diagnostic information for debugging and troubleshooting,
with proper redaction of sensitive data.
"""

import logging
import re
from typing import Any, Final

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    DIAG_API_STATUS,
    DIAG_AVAILABLE,
    DIAG_CONNECTIVITY,
    DIAG_COORDINATOR,
    DIAG_COORDINATOR_AVAILABLE,
    DIAG_DATA,
    DIAG_DEVICE,
    DIAG_DEVICE_AVAILABLE,
    DIAG_DEVICE_DATA,
    DIAG_DEVICE_DATA_ERROR,
    DIAG_DEVICE_REGISTERED,
    DIAG_DISCOVERY_COMPLETED,
    DIAG_DOMAIN,
    DIAG_ENTRY,
    DIAG_IS_AVAILABLE,
    DIAG_MINOR_VERSION,
    DIAG_TITLE,
    DIAG_VERSION,
    KEY_API_KEY,
    KEY_PASSWORD,
    KEY_SECRET,
    KEY_SERIAL,
    KEY_TOKEN,
)
from .helpers.ha_types import ESPConfigEntry
from .iot.specs.keys import (
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
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

_LOGGER = logging.getLogger(__name__)

# Regex pattern for MAC addresses (xx:xx:xx:xx:xx:xx or xx-xx-xx-xx-xx-xx)
_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")

# Keys to redact from diagnostics output
TO_REDACT: Final[set[str]] = {
    CONF_CUSTOM_POP,
    KEY_POP,
    KEY_PASSWORD,
    KEY_TOKEN,
    KEY_SECRET,
    KEY_API_KEY,
    KEY_MAC,
    KEY_SERIAL,
}

# Keys to partially redact (show partial value)
TO_PARTIALLY_REDACT: Final[set[str]] = {
    CONF_HOST,
    KEY_IP,
    "ip_address",  # Used by some external data structures
}


def _redact_mac_address(value: str) -> str:
    """Redact MAC address if the value matches MAC pattern.

    Args:
        value: The string value to check.

    Returns:
        "REDACTED_MAC" if value is a MAC address, original value otherwise.
    """
    if _MAC_PATTERN.match(value):
        return "REDACTED_MAC"
    return value


def _redact_identifier(identifier: tuple) -> list:
    """Redact sensitive data from device identifier tuple.

    Args:
        identifier: A tuple like (domain, unique_id) from device.identifiers.

    Returns:
        A list with MAC addresses redacted.
    """
    result = []
    for item in identifier:
        if isinstance(item, str):
            result.append(_redact_mac_address(item))
        else:
            result.append(item)
    return result


def _redact_ip_address(ip: str) -> str:
    """Partially redact an IPv4 address, showing only the last octet.

    Note: ESP Local Control only supports IPv4 addresses.

    Args:
        ip: The IP address to redact.

    Returns:
        Partially redacted IP address (e.g., "***.***.***.100").
    """
    if not ip:
        return "**REDACTED**"

    # IPv4 address (contains dots)
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4 and all(
            part.isdigit() and 0 <= int(part) <= 255 for part in parts
        ):
            return f"***.***.***.{parts[3]}"

    return "**REDACTED**"


def _redact_list_item(
    item: Any,
    to_redact: set[str],
    to_partially_redact: set[str],
) -> Any:
    """Redact a single item from a list.

    Args:
        item: The item to potentially redact.
        to_redact: Keys to fully redact (only applies to dict keys).
        to_partially_redact: Keys to partially redact (only applies to dict keys).

    Returns:
        Redacted item or original item if no redaction needed.
    """
    if isinstance(item, dict):
        return _process_dict_for_redaction(item, to_redact, to_partially_redact)
    if isinstance(item, list):
        return [_redact_list_item(i, to_redact, to_partially_redact) for i in item]
    # Only redact dict keys, not arbitrary string values in lists
    return item


def _process_dict_for_redaction(
    data: dict[str, Any],
    to_redact: set[str],
    to_partially_redact: set[str],
) -> dict[str, Any]:
    """Process a dictionary, redacting sensitive keys.

    Args:
        data: Dictionary to process.
        to_redact: Keys to fully redact.
        to_partially_redact: Keys to partially redact.

    Returns:
        Processed dictionary with redacted values.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_lower = key.lower()

        # Security: Use substring matching for full redaction to err on the side
        # of over-redaction. This may redact keys like "machine_id" (contains "mac")
        # or "authentication_token_info" (contains "token"), but it's safer than
        # potentially leaking sensitive data. Partial redaction uses exact matching
        # since IP addresses are less sensitive and over-redaction would reduce
        # diagnostic utility.
        if key_lower in to_redact or any(r in key_lower for r in to_redact):
            result[key] = "**REDACTED**"
        elif key_lower in to_partially_redact:
            if isinstance(value, str) and "." in value:
                # Handle IPv4 addresses (ESP Local Control only supports IPv4)
                result[key] = _redact_ip_address(value)
            else:
                result[key] = "**REDACTED**"
        elif isinstance(value, dict):
            result[key] = _process_dict_for_redaction(
                value, to_redact, to_partially_redact
            )
        elif isinstance(value, list):
            result[key] = [
                _redact_list_item(item, to_redact, to_partially_redact)
                for item in value
            ]
        else:
            result[key] = value

    return result


async def _get_redacted_device_data(api: Any, node_id: str) -> dict[str, Any]:
    """Get device data with redaction applied.

    Args:
        api: The ESP-Weaver API instance.
        node_id: The device node ID.

    Returns:
        Dictionary with redacted device data, or error info if retrieval failed.
    """
    try:
        device_data = await api.get_device_data(node_id)
    except (OSError, TimeoutError, RuntimeError, AttributeError) as err:
        _LOGGER.error("Failed to retrieve device data for diagnostics: %s", err)
        return {DIAG_DEVICE_DATA_ERROR: "Failed to retrieve device data"}

    if device_data:
        return {
            DIAG_DEVICE_DATA: _process_dict_for_redaction(
                device_data, TO_REDACT, TO_PARTIALLY_REDACT
            )
        }
    return {}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: ESPConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry to get diagnostics for.

    Returns:
        Dictionary containing diagnostic information.
    """
    coordinator = entry.runtime_data
    node_id = entry.data.get(CONF_NODE_ID)

    diagnostics_data: dict[str, Any] = {
        DIAG_ENTRY: {
            DIAG_TITLE: entry.title,
            DIAG_DOMAIN: entry.domain,
            DIAG_VERSION: entry.version,
            DIAG_MINOR_VERSION: entry.minor_version,
            DIAG_DATA: _process_dict_for_redaction(
                dict(entry.data), TO_REDACT, TO_PARTIALLY_REDACT
            ),
        },
        DIAG_COORDINATOR: {
            DIAG_AVAILABLE: bool(coordinator),
            DIAG_DISCOVERY_COMPLETED: coordinator.discovery_completed
            if coordinator
            else False,
            DIAG_IS_AVAILABLE: coordinator.is_available if coordinator else False,
        },
    }

    # Add API status if coordinator exists
    if node_id and coordinator:
        api = coordinator.api

        diagnostics_data[DIAG_API_STATUS] = {
            DIAG_DEVICE_REGISTERED: node_id in api.devices,
            DIAG_DEVICE_AVAILABLE: api.is_device_available(node_id),
            DIAG_DISCOVERY_COMPLETED: api.is_discovery_completed(node_id),
        }

        # Get device data with redaction using shared helper
        device_data_result = await _get_redacted_device_data(api, node_id)
        diagnostics_data.update(device_data_result)

    return diagnostics_data


async def async_get_device_diagnostics(
    _hass: HomeAssistant,
    entry: ESPConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a device entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry for this device.
        device: The device entry to get diagnostics for.

    Returns:
        Dictionary containing device diagnostic information.
    """
    coordinator = entry.runtime_data
    node_id = entry.data.get(CONF_NODE_ID)

    # Device info
    diagnostics_data: dict[str, Any] = {
        DIAG_DEVICE: {
            KEY_NAME: device.name,
            KEY_MODEL: device.model,
            KEY_MANUFACTURER: device.manufacturer,
            KEY_SW_VERSION: device.sw_version,
            KEY_HW_VERSION: device.hw_version,
            KEY_IDENTIFIERS: [_redact_identifier(i) for i in device.identifiers],
        },
        DIAG_ENTRY: {
            DIAG_TITLE: entry.title,
            CONF_NODE_ID: node_id,
        },
    }

    # Add device-specific data
    if node_id and coordinator:
        api = coordinator.api

        diagnostics_data[DIAG_CONNECTIVITY] = {
            DIAG_AVAILABLE: api.is_device_available(node_id),
            DIAG_COORDINATOR_AVAILABLE: coordinator.is_available,
        }

        # Get device data with redaction using shared helper
        device_data_result = await _get_redacted_device_data(api, node_id)
        diagnostics_data.update(device_data_result)

    return diagnostics_data
