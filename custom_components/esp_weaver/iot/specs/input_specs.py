# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Interactive input specifications and constants."""

from typing import Final

# Note: Field keys (KEY_INPUT_TYPE, KEY_LAST_EVENT, etc.) are defined in keys.py

# =============================================================================
# INPUT TYPE CONSTANTS
# =============================================================================

INPUT_TYPE_BUTTON: Final[str] = "button"
INPUT_TYPE_ROTARY: Final[str] = "rotary"
INPUT_TYPE_TOUCH: Final[str] = "touch"

# =============================================================================
# INPUT EVENT CONSTANTS
# =============================================================================

# Common event state when no event has occurred
INPUT_EVENT_NONE: Final[str] = "none"

# Button events
INPUT_EVENT_CLICK: Final[str] = "click"
INPUT_EVENT_DOUBLE_CLICK: Final[str] = "double_click"
INPUT_EVENT_LONG_PRESS: Final[str] = "long_press"

# Rotary events
INPUT_EVENT_ANGLE: Final[str] = "angle"
INPUT_EVENT_INCREMENT: Final[str] = "increment"
INPUT_EVENT_SPEED: Final[str] = "speed"

# Touch events
INPUT_EVENT_TAP: Final[str] = "tap"
INPUT_EVENT_DOUBLE_TAP: Final[str] = "double_tap"
INPUT_EVENT_SWIPE: Final[str] = "swipe"

# All supported input events (for entity options)
INPUT_EVENT_OPTIONS: Final[tuple[str, ...]] = (
    INPUT_EVENT_NONE,
    # Button events
    INPUT_EVENT_CLICK,
    INPUT_EVENT_DOUBLE_CLICK,
    INPUT_EVENT_LONG_PRESS,
    # Rotary events
    INPUT_EVENT_ANGLE,
    INPUT_EVENT_INCREMENT,
    INPUT_EVENT_SPEED,
    # Touch events
    INPUT_EVENT_TAP,
    INPUT_EVENT_DOUBLE_TAP,
    INPUT_EVENT_SWIPE,
)

# =============================================================================
# INPUT ICON MAPPING
# =============================================================================

# Input icon mapping - icons for different input types and events
INPUT_ICON_MAPPING: Final[dict[str, dict[str, str]]] = {
    INPUT_TYPE_BUTTON: {
        INPUT_EVENT_CLICK: "mdi:gesture-tap",
        INPUT_EVENT_DOUBLE_CLICK: "mdi:gesture-double-tap",
        INPUT_EVENT_LONG_PRESS: "mdi:gesture-tap-hold",
    },
    INPUT_TYPE_ROTARY: {
        INPUT_EVENT_ANGLE: "mdi:rotate-3d",
        INPUT_EVENT_INCREMENT: "mdi:plus-minus",
        INPUT_EVENT_SPEED: "mdi:speedometer",
    },
    INPUT_TYPE_TOUCH: {
        INPUT_EVENT_TAP: "mdi:gesture-tap",
        INPUT_EVENT_DOUBLE_TAP: "mdi:gesture-double-tap",
        INPUT_EVENT_LONG_PRESS: "mdi:gesture-tap-hold",
        INPUT_EVENT_SWIPE: "mdi:gesture-swipe",
    },
}
