# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Property parsing utilities for ESP IoT integration.

This module provides utility functions for parsing device properties
from ESP Local Control protocol responses.

These functions are used by:
- DeviceDiscoveryManager for initial property extraction
- DeviceMonitor for active report processing
- PropertyManager for property updates

Note: If you need both raw_params and by_device_type values, call
parse_device_properties() directly instead of using both get_raw_params()
and get_device_states() convenience functions separately. This avoids
parsing the properties twice.
"""

from dataclasses import dataclass
import json
import logging
from typing import Any

from ..specs.device_specs import DEVICE_KEY_STATE, DEVICE_TYPE_BINARY_SENSOR
from ..specs.keys import KEY_CONFIG, KEY_NAME, KEY_PARAMS, KEY_VALUE

_LOGGER = logging.getLogger(__name__)


@dataclass
class ParsedProperties:
    """Result of parsing device properties.

    Attributes:
        raw_params: Raw params data from the first "params" property found.
                   Used for active reports where we need the original structure.
        by_device_type: Values organized by device type (Light, Sensor, etc.).
                       Used for discovery and state sync.
    """

    raw_params: dict[str, Any] | None
    by_device_type: dict[str, Any]


def decode_property_value(prop_value: bytes | str | dict | None) -> dict | None:
    """Decode property value to dictionary.

    Handles various formats from ESP devices:
    - bytes (UTF-8 or Latin-1 encoded JSON)
    - str (JSON string)
    - dict (already parsed)

    Args:
        prop_value: Raw property value from device

    Returns:
        Parsed dictionary or None if parsing fails
    """
    if prop_value is None:
        return None

    if isinstance(prop_value, dict):
        return prop_value

    if isinstance(prop_value, bytes):
        # ESP devices send UTF-8 encoded JSON
        try:
            value_str = prop_value.decode("utf-8")
        except UnicodeDecodeError:
            # Latin-1 can decode any byte sequence
            value_str = prop_value.decode("latin-1")

        value_str = value_str.strip()
        if not value_str.startswith("{"):
            return None

        try:
            result = json.loads(value_str)
        except json.JSONDecodeError:
            _LOGGER.debug("Failed to parse JSON from bytes property value")
            return None
        if not isinstance(result, dict):
            _LOGGER.debug("JSON parsed to non-dict type: %s", type(result).__name__)
            return None
        return result

    if isinstance(prop_value, str):
        prop_value = prop_value.strip()
        try:
            if prop_value.startswith("{"):
                str_result = json.loads(prop_value)
                if not isinstance(str_result, dict):
                    _LOGGER.debug(
                        "JSON string parsed to non-dict type: %s",
                        type(str_result).__name__,
                    )
                    return None
                return str_result
        except json.JSONDecodeError:
            _LOGGER.debug("Failed to parse JSON string property value")
            return None

    return None


def parse_device_properties(
    properties: list[dict[str, Any]] | None,
) -> ParsedProperties:
    """Parse device properties and extract values.

    This function processes ESP device properties and returns both:
    1. Raw params data (for active reports)
    2. Values organized by device type (for discovery/sync)

    Args:
        properties: List of property dictionaries from device.
                   Each dict has "name" and "value" keys.
                   Can be None or empty list.

    Returns:
        ParsedProperties with:
        - raw_params: First "params" property value found (or None)
        - by_device_type: Values organized by device type, e.g.:
            {
                "Light": {"Power": True, "Brightness": 100},
                "Temperature Sensor": {"Temperature": 25.5},
            }

    Example:
        >>> result = parse_device_properties(properties)
        >>> # For active reports:
        >>> if result.raw_params:
        ...     process_update(result.raw_params)
        >>> # For discovery:
        >>> light_data = result.by_device_type.get("Light", {})
    """
    # Handle None or empty input
    if not properties:
        return ParsedProperties(raw_params=None, by_device_type={})

    raw_params: dict[str, Any] | None = None
    by_device_type: dict[str, Any] = {}

    for prop in properties:
        prop_name = prop.get(KEY_NAME, "")

        # Skip config property (it's device definition, not state)
        if prop_name == KEY_CONFIG:
            continue

        prop_value = prop.get(KEY_VALUE)
        if prop_value is None:
            continue

        decoded = decode_property_value(prop_value)
        if not decoded:
            continue

        # Capture first params property as raw_params (ESP may report without name)
        if raw_params is None and (not prop_name or prop_name == KEY_PARAMS):
            raw_params = decoded

        # Organize all data by device type
        _organize_by_device_type(decoded, by_device_type)

    return ParsedProperties(raw_params=raw_params, by_device_type=by_device_type)


def _organize_by_device_type(
    decoded_data: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Organize decoded property data by device type.

    Args:
        decoded_data: Decoded property value dictionary
        result: Dictionary to populate with organized data
    """
    for device_type, device_data in decoded_data.items():
        # Skip non-dict values (metadata fields)
        if not isinstance(device_data, dict):
            # Handle "State" for binary sensors - store under binary sensor namespace
            if device_type == DEVICE_KEY_STATE:
                result.setdefault(DEVICE_TYPE_BINARY_SENSOR, {})[DEVICE_KEY_STATE] = (
                    device_data
                )
            continue

        # Store or merge device data
        if device_type not in result:
            result[device_type] = device_data
        else:
            result[device_type].update(device_data)


# Convenience Functions


def get_device_states(properties: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Get device states organized by device type.

    Convenience wrapper for parse_device_properties().by_device_type.

    Args:
        properties: List of property dictionaries from device.
                   Can be None or empty list.

    Returns:
        Dictionary of states by device type, e.g.:
        {"Light": {"Power": True}, "Temperature Sensor": {"Temperature": 25.5}}
        Returns empty dict if properties is None or empty.
    """
    if not properties:
        return {}
    return parse_device_properties(properties).by_device_type


def get_raw_params(properties: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Get raw params data from properties.

    Convenience wrapper for parse_device_properties().raw_params.

    Args:
        properties: List of property dictionaries.
                   Can be None or empty list.

    Returns:
        Raw params dict or None if not found or input is empty.
    """
    if not properties:
        return None
    return parse_device_properties(properties).raw_params
