# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Sleep utility functions."""

from ..specs.sleep_specs import SLEEP_ICON_MAPPING

# Default icon for unknown sleep states
ICON_UNKNOWN: str = "mdi:help-circle"


def get_sleep_icon(sleep_state: str) -> str:
    """Get icon for sleep state.

    Args:
        sleep_state: Current sleep state

    Returns:
        Material Design icon identifier
    """
    return SLEEP_ICON_MAPPING.get(sleep_state, ICON_UNKNOWN)
