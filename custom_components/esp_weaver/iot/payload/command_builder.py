# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Command builders (HA → ESP).

This module builds commands sent TO ESP devices.
Used when UI controls device properties (light control, threshold settings).

Note: This differs from event_payload_builder.py which builds event data for
internal HA events (ESP → HA event system).
"""

from collections.abc import Callable
import logging
import threading
from typing import Any

from ..specs.device_specs import DEVICE_TYPE_LIGHT, DEVICE_TYPE_TEMPERATURE_SENSOR
from ..specs.light_specs import LIGHT_PARAM_KEYWORDS, LIGHT_PARAM_MAP
from ..specs.sensor_specs import SENSOR_PARAM_KEYWORDS

_LOGGER = logging.getLogger(__name__)

# Type alias for command builder function
CommandBuilder = Callable[[str, Any], dict[str, Any]]

# Type alias for matcher function
ParamMatcher = Callable[[str], bool]


# Command Builders


def build_light_command(param_name: str, param_value: Any) -> dict[str, Any]:
    """Build light device command.

    Args:
        param_name: Light parameter name (e.g., "power", "brightness").
        param_value: Light parameter value.

    Returns:
        Formatted command: {"Light": {"Power": true}} etc.
    """
    lower_name = param_name.lower()
    param_key = LIGHT_PARAM_MAP.get(lower_name, lower_name)
    return {DEVICE_TYPE_LIGHT: {param_key: param_value}}


def build_threshold_command(param_name: str, param_value: Any) -> dict[str, Any]:
    """Build threshold setting command for Temperature Sensor.

    Args:
        param_name: Threshold parameter name (e.g., "temperature_min_threshold").
        param_value: Threshold value.

    Returns:
        Formatted command: {"Temperature Sensor": {"..._threshold": 20}}.
    """
    return {DEVICE_TYPE_TEMPERATURE_SENSOR: {param_name: param_value}}


# Matcher Functions


def _match_light_param(param_name_lower: str) -> bool:
    """Check if parameter is a light parameter."""
    return param_name_lower in LIGHT_PARAM_KEYWORDS


def _match_sensor_param(param_name_lower: str) -> bool:
    """Check if parameter is a sensor/threshold parameter.

    Uses token-based matching to avoid false positives (e.g., "temp" in "temporary").
    Tokens are split on underscores and hyphens.
    """
    # Split param name into tokens for boundary matching
    tokens = set(param_name_lower.replace("-", "_").split("_"))
    return bool(tokens & SENSOR_PARAM_KEYWORDS)


# Device Type Registry

# Lock for thread-safe registry modifications
_REGISTRY_LOCK = threading.Lock()

# Registry of device types with their matchers and command builders
# Format: (matcher_function, command_builder_function)
# Order matters - first match wins
_DEVICE_TYPE_REGISTRY: list[tuple[ParamMatcher, CommandBuilder]] = [
    (_match_light_param, build_light_command),
    (_match_sensor_param, build_threshold_command),
]


# Main Entry Point


def build_device_command(param_name: str, param_value: Any) -> dict[str, Any] | None:
    """Build ESP-RainMaker device command based on parameter name.

    Uses registered device type handlers to route to appropriate command builder.

    Args:
        param_name: Parameter name to determine device type (must be a string).
        param_value: Parameter value to include in command.

    Returns:
        ESP-RainMaker JSON command dictionary, or None if not recognized.

    Raises:
        TypeError: If param_name is not a string.
    """
    if not isinstance(param_name, str):
        raise TypeError(f"param_name must be a str, got {type(param_name).__name__}")
    param_name_lower = param_name.lower()

    # Copy registry under lock for thread-safe iteration
    with _REGISTRY_LOCK:
        registry_snapshot = list(_DEVICE_TYPE_REGISTRY)

    # Check registered device types
    for matcher, builder in registry_snapshot:
        if matcher(param_name_lower):
            return builder(param_name, param_value)

    _LOGGER.warning("Unknown parameter type: %s", param_name)
    return None
