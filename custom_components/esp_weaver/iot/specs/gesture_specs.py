# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Gesture sensor specifications and constants."""

from typing import Final

# Note: Field keys (KEY_GESTURE_TYPE, KEY_SENSITIVITY, etc.) are defined in keys.py

# Gesture Type Constants

GESTURE_IDLE: Final[str] = "idle"
GESTURE_SHAKE: Final[str] = "shake"
GESTURE_PUSH: Final[str] = "push"
GESTURE_CIRCLE: Final[str] = "circle"
GESTURE_FLIP: Final[str] = "flip"
GESTURE_TOSS: Final[str] = "toss"
GESTURE_ROTATION: Final[str] = "rotation"
GESTURE_CLAP_SINGLE: Final[str] = "clap_single"
GESTURE_CLAP_DOUBLE: Final[str] = "clap_double"
GESTURE_CLAP_TRIPLE: Final[str] = "clap_triple"

# Gesture icon mapping - only for supported gestures
GESTURE_ICONS: Final[dict[str, str]] = {
    GESTURE_IDLE: "mdi:gesture-tap",
    GESTURE_TOSS: "mdi:arrow-up-bold",
    GESTURE_FLIP: "mdi:flip-horizontal",
    GESTURE_SHAKE: "mdi:vibrate",
    GESTURE_ROTATION: "mdi:rotate-3d-variant",
    GESTURE_PUSH: "mdi:gesture-swipe-right",
    GESTURE_CIRCLE: "mdi:circle-outline",
    GESTURE_CLAP_SINGLE: "mdi:hand-clap",
    GESTURE_CLAP_DOUBLE: "mdi:hand-clap",
    GESTURE_CLAP_TRIPLE: "mdi:hand-clap",
}

# Default gesture icon
DEFAULT_GESTURE_ICON: Final[str] = "mdi:gesture-tap"
