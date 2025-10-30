# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Light utility functions."""

import logging
from typing import Any

from ..specs.device_specs import ESP_PROP_POWER
from ..specs.keys import (
    KEY_BRIGHTNESS,
    KEY_HS_COLOR,
    KEY_HUE,
    KEY_INTENSITY,
    KEY_IS_ON,
    KEY_LIGHT_MODE,
    KEY_POWER,
    KEY_SATURATION,
)
from ..specs.light_specs import (
    ESP_PROP_BRIGHTNESS,
    ESP_PROP_HUE,
    ESP_PROP_LIGHT_MODE,
    ESP_PROP_SATURATION,
    LIGHT_BRIGHTNESS_ESP_MAX,
    LIGHT_BRIGHTNESS_HA_MAX,
)

_LOGGER = logging.getLogger(__name__)

# Brightness Conversion Functions


def convert_brightness_to_ha(brightness_esp: float) -> int:
    """Convert ESP brightness (0-100) to Home Assistant brightness (0-255).

    Args:
        brightness_esp: Brightness value from ESP device (0-100 range)

    Returns:
        Brightness value for Home Assistant (0-255 range), clamped to valid range
    """
    original = brightness_esp
    brightness_esp = max(0, min(LIGHT_BRIGHTNESS_ESP_MAX, brightness_esp))
    if original != brightness_esp:
        _LOGGER.debug(
            "ESP brightness clamped from %s to %s (valid range: 0-%s)",
            original,
            brightness_esp,
            LIGHT_BRIGHTNESS_ESP_MAX,
        )
    return round(brightness_esp * LIGHT_BRIGHTNESS_HA_MAX / LIGHT_BRIGHTNESS_ESP_MAX)


def convert_brightness_to_esp(brightness_ha: int) -> int:
    """Convert Home Assistant brightness (0-255) to ESP brightness (0-100).

    Args:
        brightness_ha: Brightness value from Home Assistant (0-255 range)

    Returns:
        Brightness value for ESP device (0-100 range), clamped to valid range
    """
    original = brightness_ha
    brightness_ha = max(0, min(LIGHT_BRIGHTNESS_HA_MAX, brightness_ha))
    if original != brightness_ha:
        _LOGGER.debug(
            "HA brightness clamped from %s to %s (valid range: 0-%s)",
            original,
            brightness_ha,
            LIGHT_BRIGHTNESS_HA_MAX,
        )
    return round(brightness_ha * LIGHT_BRIGHTNESS_ESP_MAX / LIGHT_BRIGHTNESS_HA_MAX)


# Light Mode Parsing


def parse_light_mode(effect: str | None) -> int | None:
    """Parse light mode number from effect string.

    Args:
        effect: Effect string in format "Mode N" where N is 0-5

    Returns:
        Mode number (0-5) if valid, None otherwise
    """
    if not isinstance(effect, str):
        return None
    try:
        if not effect.startswith("Mode "):
            return None
        mode_num = int(effect.split()[-1])
        if 0 <= mode_num <= 5:
            return mode_num
    except (ValueError, IndexError):
        pass
    return None


# Light Parameter Parsing


def parse_light_update(
    light_data: dict[str, Any], current_state: dict[str, Any]
) -> dict[str, Any]:
    """Parse light update data and return state changes.

    Args:
        light_data: Light update data from device (only contains non-None values)
        current_state: Current light state

    Returns:
        Dictionary with updated light state attributes
    """
    updates = {}

    if KEY_POWER in light_data:
        updates[KEY_IS_ON] = light_data[KEY_POWER]

    if KEY_BRIGHTNESS in light_data:
        updates[KEY_BRIGHTNESS] = convert_brightness_to_ha(light_data[KEY_BRIGHTNESS])

    # Support partial hue/saturation updates
    if KEY_HUE in light_data or KEY_SATURATION in light_data:
        current_hs = current_state.get(KEY_HS_COLOR) or (0, 0)
        new_hue = light_data.get(KEY_HUE, current_hs[0])
        new_saturation = light_data.get(KEY_SATURATION, current_hs[1])
        updates[KEY_HS_COLOR] = (new_hue, new_saturation)

    if KEY_INTENSITY in light_data:
        updates[KEY_INTENSITY] = light_data[KEY_INTENSITY]
    if KEY_LIGHT_MODE in light_data:
        updates[KEY_LIGHT_MODE] = light_data[KEY_LIGHT_MODE]

    return updates


# Light Control Property Builders


def build_light_turn_on_properties(
    brightness: int | None = None,
    hs_color: tuple[float, float] | None = None,
    effect: str | None = None,
) -> dict[str, Any]:
    """Build properties dictionary for turning on a light.

    Args:
        brightness: HA brightness value (0-255) or None.
        hs_color: Tuple of (hue, saturation) or None.
        effect: Effect string (e.g., "Mode 0") or None.

    Returns:
        Dictionary of properties to send to device.
    """
    properties: dict[str, Any] = {ESP_PROP_POWER: True}

    if brightness is not None:
        properties[ESP_PROP_BRIGHTNESS] = convert_brightness_to_esp(brightness)

    if hs_color is not None:
        hue, saturation = hs_color
        clamped_hue = max(0, min(360, int(hue)))
        clamped_saturation = max(0, min(100, int(saturation)))
        properties[ESP_PROP_HUE] = clamped_hue
        properties[ESP_PROP_SATURATION] = clamped_saturation

    if effect is not None:
        mode_num = parse_light_mode(effect)
        if mode_num is not None:
            properties[ESP_PROP_LIGHT_MODE] = mode_num

    return properties


def build_light_turn_off_properties() -> dict[str, Any]:
    """Build properties dictionary for turning off a light.

    Returns:
        Dictionary with Power set to False.
    """
    return {ESP_PROP_POWER: False}
