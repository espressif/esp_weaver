# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Binary sensor utility functions."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

from ..specs.binary_sensor_specs import (
    BINARY_SENSOR_DEVICE_CLASS_MAP,
    DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
)
from ..specs.keys import (
    KEY_DEBOUNCE_TIME,
    KEY_DEVICE_CLASS,
    KEY_PARAMS,
    KEY_REPORT_INTERVAL,
    KEY_SENSOR_VALUE,
)

_LOGGER = logging.getLogger(__name__)


def get_binary_sensor_device_class(
    device_class_str: str | Any, default: str = DEFAULT_BINARY_SENSOR_DEVICE_CLASS
) -> str:
    """Get normalized binary sensor device class from string identifier.

    Args:
        device_class_str: Device class identifier string from device configuration
        default: Default device class to use if lookup fails

    Returns:
        Normalized device class string that matches BinarySensorDeviceClass enum values
    """
    if not device_class_str:
        return default

    # Type check - must be a string
    if not isinstance(device_class_str, str):
        _LOGGER.warning(
            "Invalid device_class type: %s (expected str), using default",
            type(device_class_str).__name__,
        )
        return default

    # Normalize input
    normalized = device_class_str.lower().strip()

    if not normalized:
        return default

    # Look up in mapping
    return BINARY_SENSOR_DEVICE_CLASS_MAP.get(normalized, default)


@dataclass
class BinarySensorUpdateResult:
    """Result of processing a binary sensor update.

    Attributes:
        state: New state (True = on, False = off, None = unchanged).
        device_class: New device class or None if unchanged.
        debounce_time: Debounce time value or None if not provided.
        report_interval: Report interval value or None if not provided.
        has_changes: Whether any values actually changed.
    """

    state: bool | None = None
    device_class: str | None = None
    debounce_time: int | None = None
    report_interval: int | None = None
    has_changes: bool = False


def process_binary_sensor_update(
    state_data: Mapping[str, Any],
    current_state: bool | None,
    current_device_class: str,
    default_device_class: str = DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
    current_debounce_time: int | None = None,
    current_report_interval: int | None = None,
) -> BinarySensorUpdateResult:
    """Process binary sensor update data.

    Args:
        state_data: Event data dictionary.
        current_state: Current state of the sensor (None for unknown).
        current_device_class: Current device class.
        default_device_class: Default device class if lookup fails.
        current_debounce_time: Current debounce time value for comparison.
        current_report_interval: Current report interval value for comparison.

    Returns:
        BinarySensorUpdateResult with all processed data.
    """
    result = BinarySensorUpdateResult()

    # Process state change
    new_state = state_data.get(KEY_SENSOR_VALUE)
    if new_state is not None:
        new_state_bool = bool(new_state)
        if current_state is None or new_state_bool != current_state:
            result.state = new_state_bool
            result.has_changes = True

    # Process device class change
    params = state_data.get(KEY_PARAMS) or {}
    device_class = params.get(KEY_DEVICE_CLASS)
    if device_class:
        if isinstance(device_class, bytes):
            device_class = device_class.decode("utf-8", errors="replace")

        device_class_normalized = get_binary_sensor_device_class(
            str(device_class), default_device_class
        )

        if device_class_normalized != current_device_class:
            result.device_class = device_class_normalized
            result.has_changes = True

    # Process optional config attributes - only set when value differs
    debounce_time = params.get(KEY_DEBOUNCE_TIME)
    if debounce_time is not None:
        try:
            debounce_value = int(debounce_time)
            if debounce_value < 0:
                _LOGGER.warning(
                    "Invalid debounce_time value (negative): %s", debounce_time
                )
            elif debounce_value != current_debounce_time:
                result.debounce_time = debounce_value
                result.has_changes = True
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid debounce_time value: %s", debounce_time)

    report_interval = params.get(KEY_REPORT_INTERVAL)
    if report_interval is not None:
        try:
            interval_value = int(report_interval)
            if interval_value < 0:
                _LOGGER.warning(
                    "Invalid report_interval value (negative): %s", report_interval
                )
            elif interval_value != current_report_interval:
                result.report_interval = interval_value
                result.has_changes = True
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid report_interval value: %s", report_interval)

    return result
