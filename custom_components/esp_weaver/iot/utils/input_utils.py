# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Input utility functions."""

from ..specs.input_specs import INPUT_ICON_MAPPING
from ..specs.keys import (
    KEY_INPUT_CONFIG,
    KEY_INPUT_EVENTS,
    KEY_INPUT_MAPPING,
    KEY_INPUT_TYPE,
    KEY_INPUT_VALUE,
    KEY_LAST_EVENT,
    KEY_SENSITIVITY,
)

# Default icon for interactive inputs when no specific icon is found
DEFAULT_INPUT_ICON = "mdi:gesture-tap"

# Fields that map directly from input data to updates
DIRECT_INPUT_FIELDS = (
    KEY_INPUT_TYPE,
    KEY_INPUT_VALUE,
    KEY_INPUT_CONFIG,
    KEY_INPUT_MAPPING,
    KEY_SENSITIVITY,
)


def get_input_icon(input_type: str, last_event: str) -> str:
    """Get icon for interactive input based on type and event.

    Args:
        input_type: Input type (button, rotary, touch)
        last_event: Last event that occurred

    Returns:
        Material Design icon identifier
    """
    input_type_icons = INPUT_ICON_MAPPING.get(input_type, {})
    return input_type_icons.get(last_event, DEFAULT_INPUT_ICON)


def parse_input_update(input_data: dict) -> dict:
    """Parse input data updates.

    Args:
        input_data: Input data dict from update event (non-None values only)

    Returns:
        Dictionary containing parsed input updates
    """
    updates = {}

    # Copy fields that map directly from input data
    for field in DIRECT_INPUT_FIELDS:
        if field in input_data:
            updates[field] = input_data[field]

    # Handle last_event mapping (input_events takes precedence over last_event)
    if KEY_INPUT_EVENTS in input_data:
        updates[KEY_LAST_EVENT] = input_data[KEY_INPUT_EVENTS]
    elif KEY_LAST_EVENT in input_data:
        updates[KEY_LAST_EVENT] = input_data[KEY_LAST_EVENT]

    return updates
