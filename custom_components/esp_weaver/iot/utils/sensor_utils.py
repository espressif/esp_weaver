# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Sensor utility functions."""

import re

from ..specs.sensor_specs import SENSOR_DEFINITIONS, get_base_sensor_type

# Threshold Helper Functions


def get_sensor_threshold_config(sensor_type: str) -> dict | None:
    """Get sensor threshold configuration.

    Derives threshold key names from SENSOR_DEFINITIONS to maintain
    single source of truth.

    Args:
        sensor_type: Sensor type

    Returns:
        Threshold configuration dict with min_key and max_key, or None
    """
    base_type = get_base_sensor_type(sensor_type)
    definition = SENSOR_DEFINITIONS.get(base_type)

    if not definition:
        return None

    prefix = definition.device_param_prefix
    return {
        "min_key": f"{prefix}_min_threshold",
        "max_key": f"{prefix}_max_threshold",
    }


# Threshold Violation Detection


def get_normalized_sensor_type(raw_sensor_type: str) -> str:
    """Get normalized sensor type for threshold lookups.

    Args:
        raw_sensor_type: Raw sensor type from device.

    Returns:
        Normalized sensor type for threshold matching.
    """
    return get_base_sensor_type(raw_sensor_type.lower())


def check_threshold_violation(
    current_value: float,
    min_threshold: float | None,
    max_threshold: float | None,
) -> tuple[str | None, float | None]:
    """Check if value violates threshold limits.

    Uses strict comparisons: current_value > max_threshold for high violations
    and current_value < min_threshold for low violations. Values exactly equal
    to min_threshold or max_threshold are NOT considered violations.

    Args:
        current_value: Current sensor value.
        min_threshold: Minimum threshold (None if not set).
        max_threshold: Maximum threshold (None if not set).

    Returns:
        Tuple of (violation_type, threshold_value) where violation_type is
        "high", "low", or None.
    """
    if max_threshold is not None and current_value > max_threshold:
        return "high", max_threshold
    if min_threshold is not None and current_value < min_threshold:
        return "low", min_threshold
    return None, None


def was_previously_violated(
    old_value: float | None,
    min_threshold: float | None,
    max_threshold: float | None,
) -> bool:
    """Check if old value was in violation state.

    Args:
        old_value: Previous sensor value (None if unknown).
        min_threshold: Minimum threshold.
        max_threshold: Maximum threshold.

    Returns:
        True if old value was in violation.
    """
    if old_value is None:
        return False
    violation_type, _ = check_threshold_violation(
        old_value, min_threshold, max_threshold
    )
    return violation_type is not None


def build_threshold_notification_data(
    device_name: str,
    node_id: str,
    sensor_type: str,
    alert_type: str,
    current_value: float,
    threshold_value: float,
    unit: str,
    domain: str,
) -> dict:
    """Build notification data for threshold violation.

    Args:
        device_name: Device display name.
        node_id: Device node ID.
        sensor_type: Sensor type.
        alert_type: "high" or "low"
        current_value: Current sensor value.
        threshold_value: Violated threshold.
        unit: Unit string.
        domain: Integration domain.

    Returns:
        Dictionary with notification_id, title, and message.

    Raises:
        ValueError: If alert_type is not "high" or "low"
    """
    if alert_type not in ("high", "low"):
        raise ValueError(f"Invalid alert_type: {alert_type}. Must be 'high' or 'low'")
    # Sanitize sensor_type for use in notification ID:
    # - Replace any non-alphanumeric/underscore characters with underscore
    # - Collapse consecutive underscores
    # - Normalize to lowercase and truncate to max 32 characters
    safe_sensor_type = re.sub(r"[^A-Za-z0-9_]", "_", sensor_type)
    safe_sensor_type = re.sub(r"_+", "_", safe_sensor_type)
    safe_sensor_type = safe_sensor_type.lower().strip("_")[:32]
    notification_id = f"{domain}_threshold_{node_id}_{safe_sensor_type}_{alert_type}"
    direction = "High" if alert_type == "high" else "Low"
    display_sensor_type = sensor_type.replace("_", " ").title()
    title = f"{device_name} - {direction} {display_sensor_type}"
    message = (
        f"Warning: {display_sensor_type} is too {alert_type}: "
        f"{current_value}{unit} (threshold: {threshold_value}{unit})"
    )
    return {
        "notification_id": notification_id,
        "title": title,
        "message": message,
    }
