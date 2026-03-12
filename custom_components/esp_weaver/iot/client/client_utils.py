# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP Local Control client utility functions.

This module provides utility functions for working with ESP Local Control clients,
including value conversion, response parsing, and property handling.
"""

import json
import logging
from typing import Any

from ..specs.keys import KEY_COUNT, KEY_PROPERTIES

_LOGGER = logging.getLogger(__name__)


def convert_values_to_esp_format(values: list[Any]) -> list[bytes]:
    """Convert Python values to ESP-IDF byte format.

    Converts various Python types to the byte format expected by ESP-IDF
    Local Control protocol:
    - Bytes values are passed through unchanged (already in correct format)
    - Strings are encoded directly to bytes using UTF-8
    - Other types (int, bool, dict, etc.) are first converted to JSON strings,
      then encoded to bytes

    Args:
        values: List of Python values to convert (bytes, strings, numbers, bools,
            dicts, etc.)

    Returns:
        List of byte strings suitable for ESP-IDF protocol transmission.

    Example:
        >>> convert_values_to_esp_format([b'raw', "hello", 42, True, {"key": "value"}])
        [b'raw', b'hello', b'42', b'true', b'{"key": "value"}']
    """
    esp_values: list[bytes] = []
    for val in values:
        if isinstance(val, str):
            # String values need to be encoded to bytes
            esp_values.append(val.encode("utf-8"))
        elif isinstance(val, bytes):
            # Bytes values can be used as-is
            esp_values.append(val)
        else:
            # Other types → JSON string → bytes
            try:
                json_str = json.dumps(val, ensure_ascii=False)
                esp_values.append(json_str.encode("utf-8"))
            except (TypeError, ValueError) as err:
                _LOGGER.error(
                    "Failed to serialize value of type %s (size: %d): %s",
                    type(val).__name__,
                    len(val) if hasattr(val, "__len__") else 0,
                    err,
                )
                raise
    return esp_values


def parse_property_count_response(parsed_data: Any) -> int:
    """Parse property count from ESP Local Control response.

    Expected format: {"count": N}

    Args:
        parsed_data: Parsed response data from ESP device (typically a dict)

    Returns:
        Number of properties found in the response, or 0 if invalid/empty.
    """
    if not isinstance(parsed_data, dict):
        return 0

    count = parsed_data.get(KEY_COUNT, 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return 0

    return count


def parse_property_values_response(props_data: Any) -> list[dict[str, Any]]:
    """Parse property values from ESP Local Control response.

    Args:
        props_data: Parsed response data from ESP device

    Returns:
        List of property dictionaries. Each dictionary represents a property
        from the device response. The structure of each dictionary depends on
        the device implementation; common keys include 'name' and 'value',
        but callers should validate the presence of required keys.
    """
    if not isinstance(props_data, dict):
        return []

    if KEY_PROPERTIES in props_data:
        properties = props_data[KEY_PROPERTIES]
        if isinstance(properties, list):
            # Validate and filter items
            valid_properties = []
            for idx, prop in enumerate(properties):
                if isinstance(prop, dict):
                    valid_properties.append(prop)
                else:
                    _LOGGER.warning(
                        "Skipping non-dict property at index %d: %s",
                        idx,
                        type(prop).__name__,
                    )
            return valid_properties
        # properties key exists but is not a list
        # Log type and presence only to avoid leaking secrets
        _LOGGER.warning(
            "Malformed response: 'properties' is %s, not list (has_value=%s)",
            type(properties).__name__,
            bool(properties),
        )

    return []
