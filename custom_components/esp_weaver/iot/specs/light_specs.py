# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Light-related specifications and constants."""

from typing import Final

from .device_specs import (
    ESP_PROP_BRIGHTNESS,
    ESP_PROP_HUE,
    ESP_PROP_INTENSITY,
    ESP_PROP_POWER,
    ESP_PROP_SATURATION,
)
from .keys import (
    KEY_BRIGHTNESS,
    KEY_HUE,
    KEY_INTENSITY,
    KEY_LIGHT_MODE_INPUT,
    KEY_POWER,
    KEY_SATURATION,
)

# ESP Device Property Names (used in device communication)
# These are the actual property names as returned by ESP devices (Title Case)

ESP_PROP_LIGHT_MODE: Final[str] = "Light Mode"

# Light Parameter Mapping (lowercase input -> ESP property name)
# Used by command_builder.py to convert parameter names for device commands
# Keys and values must be unique (no aliases)

LIGHT_PARAM_MAP: Final[dict[str, str]] = {
    KEY_POWER: ESP_PROP_POWER,
    KEY_BRIGHTNESS: ESP_PROP_BRIGHTNESS,
    KEY_HUE: ESP_PROP_HUE,
    KEY_SATURATION: ESP_PROP_SATURATION,
    KEY_INTENSITY: ESP_PROP_INTENSITY,
    KEY_LIGHT_MODE_INPUT: ESP_PROP_LIGHT_MODE,
}

# Light device parameter keywords for device type detection
# Generated from LIGHT_PARAM_MAP keys to avoid redundancy
LIGHT_PARAM_KEYWORDS: Final[frozenset[str]] = frozenset(LIGHT_PARAM_MAP.keys())


# Default Light Values
# These values match ESP-RainMaker firmware defaults (app_main.c)

DEFAULT_BRIGHTNESS: Final[int] = 25
DEFAULT_HUE: Final[int] = 180
DEFAULT_SATURATION: Final[int] = 100
DEFAULT_INTENSITY: Final[int] = 25
DEFAULT_LIGHT_MODE: Final[int] = 0

# Light Effect Modes
# Note: Mode meanings depend on device firmware implementation.
# These are generic names matching the integer mode values (0-5)
# defined in ESP RainMaker light device parameters.

LIGHT_EFFECT_MODES: Final[tuple[str, ...]] = (
    "Mode 0",
    "Mode 1",
    "Mode 2",
    "Mode 3",
    "Mode 4",
    "Mode 5",
)

# Light Brightness Conversion Factors

LIGHT_BRIGHTNESS_HA_MAX: Final[int] = 255  # Home Assistant brightness range
LIGHT_BRIGHTNESS_ESP_MAX: Final[int] = 100  # ESP device brightness range
