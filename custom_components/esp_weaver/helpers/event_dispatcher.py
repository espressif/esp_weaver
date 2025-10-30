# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Event dispatching for ESP-Weaver integration."""

from collections.abc import Callable
from functools import partial
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

from ..iot.payload.event_payload_builder import (
    build_battery_event_payload,
    build_binary_sensor_event_payload,
    build_gesture_event_payload,
    build_input_event_payload,
    build_light_event_payload,
    build_sensor_event_payload,
    build_sleep_event_payload,
    build_threshold_event_payload,
)
from ..iot.specs.device_specs import (
    DEVICE_TYPE_BATTERY_ENERGY,
    DEVICE_TYPE_IMU_GESTURE,
    DEVICE_TYPE_INTERACTIVE_INPUT,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_LOW_POWER_SLEEP,
    DEVICE_TYPE_TEMPERATURE_SENSOR,
)
from ..iot.specs.events import (
    EVENT_BATTERY_ENERGY_UPDATE,
    EVENT_BINARY_SENSOR_UPDATE,
    EVENT_IMU_GESTURE_UPDATE,
    EVENT_INTERACTIVE_INPUT_UPDATE,
    EVENT_LIGHT_UPDATE,
    EVENT_LOW_POWER_SLEEP_UPDATE,
    EVENT_SENSOR_UPDATE,
    EVENT_THRESHOLD_DATA_RECEIVED,
)
from ..iot.specs.sensor_specs import THRESHOLD_KEYS

_LOGGER = logging.getLogger(__name__)


def create_event_dispatcher(
    hass: HomeAssistant,
) -> Callable[[str, dict[str, Any]], None]:
    """Create an event dispatcher function bound to hass.

    This factory function creates a dispatcher that can be injected into
    the ESPWeaverApi without creating reverse dependencies from iot to helpers.

    Args:
        hass: Home Assistant instance.

    Returns:
        A callable that takes (node_id, params_data) and fires HA events.
    """
    return partial(fire_property_events, hass)


def fire_property_events(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Fire Home Assistant UPDATE events for ESP device property updates.

    This is the main entry point for dispatching property update events.
    Called by PropertyManager when device properties change.

    Args:
        hass: Home Assistant instance
        node_id: Device node ID
        params_data: Property data dictionary keyed by device type
    """
    dispatchers = [
        _dispatch_battery_energy,
        _dispatch_interactive_input,
        _dispatch_imu_gesture,
        _dispatch_low_power_sleep,
        _dispatch_sensor,
        _dispatch_binary_sensor,
        _dispatch_light,
    ]

    for dispatcher in dispatchers:
        try:
            dispatcher(hass, node_id, params_data)
        except (KeyError, ValueError, TypeError, AttributeError) as err:
            _LOGGER.error(
                "Error dispatching events via %s for node %s: %s",
                dispatcher.__name__,
                node_id,
                err,
            )


# Internal Dispatch Functions


def _dispatch_battery_energy(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch battery & energy update event."""
    if DEVICE_TYPE_BATTERY_ENERGY not in params_data:
        return

    payload = build_battery_event_payload(
        node_id, params_data[DEVICE_TYPE_BATTERY_ENERGY]
    )
    hass.bus.async_fire(EVENT_BATTERY_ENERGY_UPDATE, payload)


def _dispatch_interactive_input(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch interactive input update event."""
    if DEVICE_TYPE_INTERACTIVE_INPUT not in params_data:
        return

    payload = build_input_event_payload(
        node_id, params_data[DEVICE_TYPE_INTERACTIVE_INPUT]
    )
    hass.bus.async_fire(EVENT_INTERACTIVE_INPUT_UPDATE, payload)


def _dispatch_imu_gesture(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch IMU gesture update event."""
    if DEVICE_TYPE_IMU_GESTURE not in params_data:
        return

    payload = build_gesture_event_payload(node_id, params_data[DEVICE_TYPE_IMU_GESTURE])
    hass.bus.async_fire(EVENT_IMU_GESTURE_UPDATE, payload)


def _dispatch_low_power_sleep(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch low power & sleep update event."""
    if DEVICE_TYPE_LOW_POWER_SLEEP not in params_data:
        return

    payload = build_sleep_event_payload(
        node_id, params_data[DEVICE_TYPE_LOW_POWER_SLEEP]
    )
    hass.bus.async_fire(EVENT_LOW_POWER_SLEEP_UPDATE, payload)


def _dispatch_sensor(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch sensor update events."""
    if DEVICE_TYPE_TEMPERATURE_SENSOR not in params_data:
        return

    env_data = params_data[DEVICE_TYPE_TEMPERATURE_SENSOR]
    for sensor_name, sensor_value in env_data.items():
        if sensor_name in THRESHOLD_KEYS:
            _fire_threshold_event(hass, node_id, sensor_name, sensor_value)
        elif isinstance(sensor_value, (int, float)):
            _fire_sensor_update(hass, node_id, sensor_name, sensor_value)
        else:
            _LOGGER.debug(
                "Skipping non-numeric sensor value for %s on node %s: %s",
                sensor_name,
                node_id,
                type(sensor_value).__name__,
            )


def _fire_threshold_event(
    hass: HomeAssistant,
    node_id: str,
    param_name: str,
    value: Any,
) -> None:
    """Fire threshold data received event."""
    payload = build_threshold_event_payload(node_id, param_name, value)
    hass.bus.async_fire(EVENT_THRESHOLD_DATA_RECEIVED, payload)


def _fire_sensor_update(
    hass: HomeAssistant,
    node_id: str,
    sensor_name: str,
    value: float,
) -> None:
    """Fire sensor update event."""
    # Robust normalization: replace any non-alphanumeric chars with underscores,
    # collapse consecutive underscores, strip leading/trailing underscores
    sensor_type = re.sub(r"[^a-zA-Z0-9]+", "_", sensor_name.lower())
    sensor_type = re.sub(r"_+", "_", sensor_type).strip("_")
    if not sensor_type:
        sensor_type = "unknown_sensor"
    payload = build_sensor_event_payload(node_id, sensor_type, value)
    hass.bus.async_fire(EVENT_SENSOR_UPDATE, payload)


def _dispatch_binary_sensor(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch binary sensor update event."""
    payload = build_binary_sensor_event_payload(node_id, params_data)
    if payload is not None:
        hass.bus.async_fire(EVENT_BINARY_SENSOR_UPDATE, payload)


def _dispatch_light(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Dispatch light update event."""
    if DEVICE_TYPE_LIGHT not in params_data:
        return

    light_data = params_data[DEVICE_TYPE_LIGHT]
    payload = build_light_event_payload(node_id, light_data)
    hass.bus.async_fire(EVENT_LIGHT_UPDATE, payload)
