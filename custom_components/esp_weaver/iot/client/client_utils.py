# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Turning values into what an ESP device expects, and its answers back again."""

import json
import logging
from typing import Any

from ..specs.keys import KEY_COUNT, KEY_PROPERTIES

_LOGGER = logging.getLogger(__name__)


def convert_values_to_esp_format(values: list[Any]) -> list[bytes]:
    """Render values as the bytes the protocol carries.

    Anything that is not already text or bytes goes as JSON, which is how the
    firmware reads a number, a boolean or a nested parameter payload.
    """
    esp_values: list[bytes] = []
    for val in values:
        if isinstance(val, str):
            esp_values.append(val.encode("utf-8"))
        elif isinstance(val, bytes):
            esp_values.append(val)
        else:
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
    """Read how many properties a device says it has, or 0 if it did not say.

    A bool is not a count.
    """
    if not isinstance(parsed_data, dict):
        return 0

    count = parsed_data.get(KEY_COUNT, 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return 0

    return count


def parse_property_values_response(props_data: Any) -> list[dict[str, Any]]:
    """Return the properties in a device's answer, keeping only the dicts."""
    if not isinstance(props_data, dict):
        return []

    if KEY_PROPERTIES in props_data:
        properties = props_data[KEY_PROPERTIES]
        if isinstance(properties, list):
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
