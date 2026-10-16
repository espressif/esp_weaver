# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Bidirectional conversion between device values and Home Assistant values.

``invert``, ``map``, ``scale`` and ``options`` all round-trip, since one
binding drives both read and write. Where that cannot hold exactly (``scale``
on an integer), the device side is authoritative and clamped to bounds.
"""

import logging
from typing import Any

from ..helpers.once import log_once
from .schema import AttributeSpec

_LOGGER = logging.getLogger(__name__)


def _invert_map(value_map: dict[Any, Any]) -> dict[Any, Any]:
    """Build the reverse of a value map; the first device value listed wins."""
    reverse: dict[Any, Any] = {}
    for device_value, ha_value in value_map.items():
        reverse.setdefault(_hashable(ha_value), device_value)
    return reverse


def _hashable(value: Any) -> Any:
    """Return a hashable stand-in for a value used as a mapping key."""
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    return value


def _scale(
    value: float,
    from_range: tuple[float, float],
    to_range: tuple[float, float],
) -> float:
    """Linearly map a value between two ranges, clamped to the target."""
    from_low, from_high = from_range
    to_low, to_high = to_range

    span = from_high - from_low
    if span == 0:
        return to_low

    ratio = (value - from_low) / span
    scaled = to_low + ratio * (to_high - to_low)
    low, high = min(to_low, to_high), max(to_low, to_high)
    return min(max(scaled, low), high)


def _scale_ranges(
    spec: AttributeSpec,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return the (device, ha) ranges for a scaled parameter."""
    if not spec.scale:
        return None
    # Both ends, since a device is free to report a partial bounds - and scaling
    # against half a range is not something to guess at.
    if not spec.bounds or "min" not in spec.bounds or "max" not in spec.bounds:
        log_once(
            _LOGGER,
            logging.WARNING,
            "Parameter %r declares scale but has no complete bounds (%s); "
            "leaving values untouched",
            spec.param,
            spec.bounds,
        )
        return None

    device_range = (float(spec.bounds["min"]), float(spec.bounds["max"]))
    target = spec.scale["to"]
    return device_range, (float(target[0]), float(target[1]))


def device_to_ha(spec: AttributeSpec, value: Any) -> Any:
    """Convert a value reported by the device into its HA representation."""
    if value is None:
        return None

    if spec.invert and isinstance(value, bool):
        value = not value

    if spec.value_map:
        key = _hashable(value)
        if key in spec.value_map:
            return spec.value_map[key]
        _LOGGER.debug(
            "Parameter %r reported unmapped value %r; passing through",
            spec.param,
            value,
        )
        return value

    ranges = _scale_ranges(spec)
    if ranges is not None and isinstance(value, (int, float)):
        device_range, ha_range = ranges
        scaled = _scale(float(value), device_range, ha_range)
        return int(round(scaled)) if _target_is_integral(*ranges) else scaled

    if spec.options and isinstance(value, str) and value not in spec.options:
        # An empty string is "nothing yet" (a status before its first report),
        # not a value outside the set.
        if not value:
            return None
        _LOGGER.debug(
            "Parameter %r reported %r which is not in its declared options",
            spec.param,
            value,
        )

    return value


def ha_to_device(spec: AttributeSpec, value: Any) -> Any:
    """Convert a value coming from Home Assistant into the device encoding."""
    if value is None:
        return None

    if spec.value_map:
        reverse = _invert_map(spec.value_map)
        key = _hashable(value)
        if key in reverse:
            return _coerce(spec, reverse[key])
        _LOGGER.debug(
            "Parameter %r received unmapped value %r; passing through",
            spec.param,
            value,
        )
        return _coerce(spec, value)

    ranges = _scale_ranges(spec)
    if ranges is not None and isinstance(value, (int, float)):
        device_range, ha_range = ranges
        value = _scale(float(value), ha_range, device_range)

    if spec.invert and isinstance(value, bool):
        value = not value

    return _coerce(spec, value)


def _target_is_integral(
    device_range: tuple[float, float],
    ha_range: tuple[float, float],
) -> bool:
    """Return True if whole numbers describe the HA side without losing detail.

    Only where the target range is at least as wide as the device's; a
    narrower one would collapse distinct device values.
    """
    if not all(float(bound).is_integer() for bound in ha_range):
        return False
    return abs(ha_range[1] - ha_range[0]) >= abs(device_range[1] - device_range[0])


def _coerce(spec: AttributeSpec, value: Any) -> Any:
    """Force a value into the parameter's declared data type."""
    if spec.data_type is None or value is None:
        return value

    try:
        if spec.data_type == "bool":
            return bool(value)
        if spec.data_type == "int":
            return int(round(float(value)))
        if spec.data_type == "float":
            return float(value)
        if spec.data_type == "string":
            return value if isinstance(value, str) else str(value)
    except (TypeError, ValueError):
        log_once(
            _LOGGER,
            logging.WARNING,
            "Cannot coerce %r to %s for parameter %r",
            value,
            spec.data_type,
            spec.param,
        )

    return value


def clamp_to_bounds(spec: AttributeSpec, value: Any) -> Any:
    """Clamp a device-side value into whichever of the parameter's bounds are known."""
    if not spec.bounds or not isinstance(value, (int, float)):
        return value

    low = spec.bounds.get("min")
    high = spec.bounds.get("max")
    clamped = value
    if low is not None:
        clamped = max(clamped, low)
    if high is not None:
        clamped = min(clamped, high)
    if clamped != value:
        _LOGGER.debug(
            "Clamped %r for parameter %r into [%s, %s]",
            value,
            spec.param,
            low,
            high,
        )
    return _coerce(spec, clamped)
