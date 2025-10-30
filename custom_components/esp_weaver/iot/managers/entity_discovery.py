# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Entity discovery management for ESP IoT integration."""

from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from ..parsers.config_parser import ESPDeviceParser
from ..parsers.property_parser import get_device_states
from ..payload.event_payload_builder import (
    build_battery_event_payload,
    build_gesture_event_payload,
    build_input_event_payload,
    build_light_event_payload,
    build_sleep_event_payload,
)
from ..specs.binary_sensor_specs import DEFAULT_BINARY_SENSOR_DEVICE_CLASS
from ..specs.device_specs import (
    CONFIG_PROPERTY_NAMES,
    DEVICE_KEY_STATE,
    DEVICE_TYPE_BATTERY_ENERGY,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_IMU_GESTURE,
    DEVICE_TYPE_INTERACTIVE_INPUT,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_LOW_POWER_SLEEP,
    DEVICE_TYPE_TEMPERATURE_SENSOR,
)
from ..specs.events import (
    EVENT_BATTERY_ENERGY_DISCOVERED,
    EVENT_BINARY_SENSOR_DISCOVERED,
    EVENT_IMU_GESTURE_DISCOVERED,
    EVENT_INTERACTIVE_INPUT_DISCOVERED,
    EVENT_LIGHT_DISCOVERED,
    EVENT_LOW_POWER_SLEEP_DISCOVERED,
    EVENT_PLATFORM_DISCOVERED,
    EVENT_SENSOR_DISCOVERED,
)
from ..specs.keys import (
    KEY_BATTERY_DATA,
    KEY_CONFIG,
    KEY_DEBOUNCE_TIME,
    KEY_DEVICE_CLASS,
    KEY_DEVICE_INFO,
    KEY_DEVICE_NAME,
    KEY_ENTITY_NAME,
    KEY_INITIAL_DATA,
    KEY_INITIAL_VALUE,
    KEY_INITIAL_VALUES,
    KEY_INPUT_DATA,
    KEY_LIGHT_DATA,
    KEY_NAME,
    KEY_NODE_ID,
    KEY_PARAM,
    KEY_PARAMS,
    KEY_PLATFORMS,
    KEY_REPORT_INTERVAL,
    KEY_SENSOR_DATA,
    KEY_SENSOR_NAME,
    KEY_SENSOR_TYPE,
    KEY_SLEEP_DATA,
    KEY_SOURCE,
    KEY_STATE,
    KEY_THRESHOLD_VALUES,
    KEY_TIMESTAMP,
    KEY_UNIT_OF_MEASUREMENT,
    KEY_VALUE,
    PLATFORM_TYPE_BATTERY_ENERGY,
    PLATFORM_TYPE_IMU_GESTURE,
    PLATFORM_TYPE_INTERACTIVE_INPUT,
    PLATFORM_TYPE_LOW_POWER_SLEEP,
)
from ..specs.sensor_specs import SENSOR_DEFINITIONS

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

# Platform types that use controller discovery pattern
CONTROLLER_PLATFORMS = frozenset(
    {
        PLATFORM_TYPE_IMU_GESTURE,
        PLATFORM_TYPE_INTERACTIVE_INPUT,
        PLATFORM_TYPE_BATTERY_ENERGY,
        PLATFORM_TYPE_LOW_POWER_SLEEP,
    }
)

# Mapping from controller platform types to their event constants
_CONTROLLER_EVENT_MAP: dict[str, str] = {
    PLATFORM_TYPE_BATTERY_ENERGY: EVENT_BATTERY_ENERGY_DISCOVERED,
    PLATFORM_TYPE_IMU_GESTURE: EVENT_IMU_GESTURE_DISCOVERED,
    PLATFORM_TYPE_INTERACTIVE_INPUT: EVENT_INTERACTIVE_INPUT_DISCOVERED,
    PLATFORM_TYPE_LOW_POWER_SLEEP: EVENT_LOW_POWER_SLEEP_DISCOVERED,
}

# Mapping from platform type to (device_type, build_function, data_key)
# Used by _convert_controller_data to transform ESP device format to internal format
_CONTROLLER_CONVERSION_MAP: dict[str, tuple[str, Callable[..., dict], str]] = {
    PLATFORM_TYPE_BATTERY_ENERGY: (
        DEVICE_TYPE_BATTERY_ENERGY,
        build_battery_event_payload,
        KEY_BATTERY_DATA,
    ),
    PLATFORM_TYPE_IMU_GESTURE: (
        DEVICE_TYPE_IMU_GESTURE,
        build_gesture_event_payload,
        KEY_SENSOR_DATA,
    ),
    PLATFORM_TYPE_INTERACTIVE_INPUT: (
        DEVICE_TYPE_INTERACTIVE_INPUT,
        build_input_event_payload,
        KEY_INPUT_DATA,
    ),
    PLATFORM_TYPE_LOW_POWER_SLEEP: (
        DEVICE_TYPE_LOW_POWER_SLEEP,
        build_sleep_event_payload,
        KEY_SLEEP_DATA,
    ),
}


@dataclass
class DiscoveryContext:
    """Context for entity discovery, containing shared parameters."""

    hass: HomeAssistant
    domain: str
    node_id: str
    device_info: dict[str, Any]
    current_values: dict[str, Any]


class DeviceDiscoveryManager:
    """Manages entity discovery for ESP IoT devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        registry: "DeviceRegistry",
    ) -> None:
        """Initialize the device discovery manager."""
        self.hass = hass
        self.domain = domain
        self.registry = registry
        self._device_parser = ESPDeviceParser(domain)

        # Note: "number" entities (thresholds) are created from sensor_discovered events
        self._discovery_handlers: dict[
            str, Callable[[DiscoveryContext, dict], None]
        ] = {
            "light": self._fire_light_discovered,
            "binary_sensor": self._fire_binary_sensor_discovered,
            "sensor": self._fire_sensor_discovered,
        }

    # Event Firing (Private)

    def _fire_event(self, event_name: str, data: dict) -> None:
        """Fire a discovery event.

        Args:
            event_name: Full event name constant (e.g., EVENT_LIGHT_DISCOVERED).
            data: Event data payload.
        """
        self.hass.bus.async_fire(event_name, data)

    def _fire_light_discovered(self, ctx: DiscoveryContext, entity_info: dict) -> None:
        """Fire light discovery event with converted data."""
        esp_light_data = ctx.current_values.get(DEVICE_TYPE_LIGHT, {})

        # Convert to internal format (same as update events)
        converted_data = {}
        if esp_light_data:
            payload = build_light_event_payload("", esp_light_data)
            converted_data = payload.get(KEY_LIGHT_DATA, {})

        self._fire_event(
            EVENT_LIGHT_DISCOVERED,
            {
                KEY_NODE_ID: ctx.node_id,
                KEY_DEVICE_NAME: entity_info.get(KEY_DEVICE_NAME, DEVICE_TYPE_LIGHT),
                KEY_LIGHT_DATA: converted_data,
                KEY_DEVICE_INFO: ctx.device_info,
            },
        )

    def _fire_binary_sensor_discovered(
        self, ctx: DiscoveryContext, entity_info: dict
    ) -> None:
        """Fire binary sensor discovery event."""
        config = entity_info.get(KEY_CONFIG, {})
        initial = entity_info.get(KEY_INITIAL_VALUES, {})

        # Priority: config > initial_values > entity_info > default
        device_class = config.get(KEY_DEVICE_CLASS)
        if not device_class:
            device_class = initial.get(KEY_DEVICE_CLASS)
        if not device_class:
            device_class = entity_info.get(
                KEY_DEVICE_CLASS, DEFAULT_BINARY_SENSOR_DEVICE_CLASS
            )
        state = self._extract_binary_sensor_state(ctx.current_values, initial)

        self._fire_event(
            EVENT_BINARY_SENSOR_DISCOVERED,
            {
                KEY_NODE_ID: ctx.node_id,
                KEY_DEVICE_NAME: entity_info.get(
                    KEY_DEVICE_NAME, DEVICE_TYPE_BINARY_SENSOR
                ),
                KEY_PARAMS: {
                    KEY_STATE: bool(state),
                    KEY_DEVICE_CLASS: device_class,
                    KEY_DEBOUNCE_TIME: initial.get(KEY_DEBOUNCE_TIME, 100),
                    KEY_REPORT_INTERVAL: initial.get(KEY_REPORT_INTERVAL, 1000),
                    KEY_TIMESTAMP: time.time(),
                    KEY_SOURCE: "params" if ctx.current_values else "discovery",
                },
                KEY_CONFIG: config,
                KEY_DEVICE_INFO: ctx.device_info,
            },
        )

    def _fire_sensor_discovered(self, ctx: DiscoveryContext, entity_info: dict) -> None:
        """Fire sensor discovery event."""
        if KEY_PARAM not in entity_info:
            return

        sensor_type = entity_info.get(KEY_SENSOR_TYPE, "")
        device_name = entity_info.get(KEY_DEVICE_NAME, "unknown")

        # Extract threshold initial values for this sensor type
        threshold_values = self._extract_threshold_values(
            ctx.current_values, sensor_type
        )

        self._fire_event(
            EVENT_SENSOR_DISCOVERED,
            {
                KEY_NODE_ID: ctx.node_id,
                KEY_SENSOR_NAME: entity_info.get(
                    KEY_SENSOR_NAME,
                    f"{device_name}_{sensor_type}",
                ),
                KEY_SENSOR_TYPE: sensor_type,
                KEY_PARAM: entity_info[KEY_PARAM],
                KEY_INITIAL_VALUE: self._extract_sensor_value(
                    ctx.current_values, entity_info
                ),
                KEY_THRESHOLD_VALUES: threshold_values,
                KEY_DEVICE_INFO: ctx.device_info,
                KEY_UNIT_OF_MEASUREMENT: entity_info.get(KEY_UNIT_OF_MEASUREMENT, ""),
                KEY_DEVICE_CLASS: entity_info.get(KEY_DEVICE_CLASS),
            },
        )

    def _fire_controller_discovered(
        self, ctx: DiscoveryContext, platform_type: str, entity_info: dict
    ) -> None:
        """Fire controller discovery event with converted data."""
        event_name = _CONTROLLER_EVENT_MAP.get(platform_type)
        if event_name is None:
            _LOGGER.warning("Unknown controller platform type: %s", platform_type)
            return

        converted_data = self._convert_controller_data(
            platform_type, ctx.current_values
        )

        self._fire_event(
            event_name,
            {
                KEY_NODE_ID: ctx.node_id,
                KEY_DEVICE_NAME: entity_info.get(
                    KEY_DEVICE_NAME,
                    ctx.device_info.get(KEY_NAME, f"{platform_type}_device"),
                ),
                KEY_ENTITY_NAME: entity_info.get(
                    KEY_ENTITY_NAME, f"{platform_type}_controller"
                ),
                KEY_PARAMS: entity_info.get(KEY_PARAMS, []),
                KEY_DEVICE_INFO: ctx.device_info,
                KEY_INITIAL_DATA: converted_data,
            },
        )

    @staticmethod
    def _convert_controller_data(platform_type: str, current_values: dict) -> dict:
        """Convert ESP device format to internal format for controller platforms."""
        if not current_values:
            return {}

        if platform_type not in _CONTROLLER_CONVERSION_MAP:
            return {}

        device_type, build_fn, data_key = _CONTROLLER_CONVERSION_MAP[platform_type]
        esp_data = current_values.get(device_type, {})

        if not esp_data:
            return {}

        # Use same build_*_event_payload function as event_dispatcher
        payload = build_fn("", esp_data)
        result: dict[str, Any] = payload.get(data_key, {})
        return result

    # Value Extraction (Static)

    @staticmethod
    def _extract_binary_sensor_state(
        current_values: dict | None, initial: dict
    ) -> bool:
        """Extract binary sensor state from values."""
        if current_values and DEVICE_TYPE_BINARY_SENSOR in current_values:
            bs_data = current_values[DEVICE_TYPE_BINARY_SENSOR]
            if isinstance(bs_data, dict) and DEVICE_KEY_STATE in bs_data:
                return bool(bs_data[DEVICE_KEY_STATE])
            if isinstance(bs_data, bool):
                return bs_data
        return bool(initial.get(KEY_STATE, False))

    @staticmethod
    def _extract_sensor_value(current_values: dict | None, entity_info: dict) -> Any:
        """Extract sensor current value from device data.

        All sensors use "Temperature Sensor" device type in ESP protocol.
        """
        if not current_values:
            return None

        if DEVICE_TYPE_TEMPERATURE_SENSOR not in current_values:
            return None

        param = entity_info.get(KEY_PARAM)
        if isinstance(param, dict):
            param_name = param.get(KEY_NAME)
        elif isinstance(param, str):
            param_name = param
        else:
            param_name = None
        if not param_name:
            return None

        sensor_data = current_values[DEVICE_TYPE_TEMPERATURE_SENSOR]
        return sensor_data.get(param_name) if isinstance(sensor_data, dict) else None

    @staticmethod
    def _extract_threshold_values(
        current_values: dict | None,
        sensor_type: str,
    ) -> dict[str, float]:
        """Extract threshold values for a sensor type from device data.

        Args:
            current_values: Current device values dictionary.
            sensor_type: Type of sensor (e.g., "temperature", "humidity").

        Returns:
            Dictionary with "min" and "max" threshold values if available.
        """
        result: dict[str, float] = {}
        if not current_values or not sensor_type:
            return result

        # Get sensor data from Temperature Sensor device type
        sensor_data = current_values.get(DEVICE_TYPE_TEMPERATURE_SENSOR, {})
        if not isinstance(sensor_data, dict):
            return result

        # Get threshold parameter prefix from sensor definitions
        sensor_def = SENSOR_DEFINITIONS.get(sensor_type.lower())
        if not sensor_def:
            return result

        prefix = sensor_def.device_param_prefix

        # Extract min and max threshold values
        min_key = f"{prefix}_min_threshold"
        max_key = f"{prefix}_max_threshold"

        with contextlib.suppress(ValueError, TypeError):
            if min_key in sensor_data:
                result["min"] = float(sensor_data[min_key])
            if max_key in sensor_data:
                result["max"] = float(sensor_data[max_key])

        return result

    # Platform & Entity Discovery

    def trigger_platform_discovery(
        self,
        node_id: str,
        device_config: dict,
        current_values: dict,
        device_info: dict,
    ) -> None:
        """Trigger platform discovery events for all platforms."""
        ctx = DiscoveryContext(
            hass=self.hass,
            domain=self.domain,
            node_id=node_id,
            device_info=device_info,
            current_values=current_values,
        )

        for platform_type, entities in device_config.get(KEY_PLATFORMS, {}).items():
            # Fire platform discovered event
            if self.registry.add_discovered_platform(platform_type):
                self._fire_event(
                    EVENT_PLATFORM_DISCOVERED,
                    {
                        "domain": platform_type,
                        "entry_id": "all",
                        "node_id": node_id,
                        KEY_DEVICE_INFO: device_info,
                    },
                )

            # Fire entity discovered events
            for entity_info in entities:
                self._trigger_entity_discovery(ctx, platform_type, entity_info)

    def _trigger_entity_discovery(
        self,
        ctx: DiscoveryContext,
        platform_type: str,
        entity_info: dict,
    ) -> None:
        """Trigger discovery for a specific entity."""
        handler = self._discovery_handlers.get(platform_type)
        if handler:
            handler(ctx, entity_info)
        elif platform_type in CONTROLLER_PLATFORMS:
            self._fire_controller_discovered(ctx, platform_type, entity_info)
        elif platform_type != "number":
            # Note: "number" entities are created from sensor_discovered events
            # (see comment at line 160)
            _LOGGER.debug(
                "Skipped entity discovery for unknown platform_type=%s, "
                "ctx.node_id=%s, entity_name=%s",
                platform_type,
                ctx.node_id,
                entity_info.get(KEY_ENTITY_NAME, "unknown"),
            )

    # Main Discovery Entry Point

    async def parse_and_discover_entities(
        self,
        node_id: str,
        properties: list | None,
        preferred_device_name: str | None = None,
    ) -> None:
        """Parse device configuration and trigger entity discovery."""
        if not properties:
            _LOGGER.warning("No properties provided for device %s", node_id)
            return

        try:
            config_property = self._find_config_property(properties)

            if preferred_device_name is None:
                preferred_device_name = self._get_preferred_device_name(node_id)

            device_config = self._parse_device_config(
                config_property, preferred_device_name
            )

            if not device_config:
                _LOGGER.warning("Device %s configuration parsing failed", node_id)
                return

            current_values = get_device_states(properties)
            device_info = device_config.get(KEY_DEVICE_INFO, {})

            _LOGGER.debug(
                "Device %s discovery - found %d device types: %s",
                node_id,
                len(current_values),
                list(current_values.keys()),
            )

            # Update device in registry
            device = self.registry.get_device(node_id)
            if device:
                device.device_info = device_info
                device.parsed_config = device_config
                device.current_values = current_values

            self.trigger_platform_discovery(
                node_id, device_config, current_values, device_info
            )

        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            # JSONDecodeError: invalid JSON in config property
            # KeyError: missing required keys in config
            # ValueError: invalid values in config
            # TypeError: wrong types in config
            _LOGGER.exception("Failed to parse device configuration for %s", node_id)

    # Config Parsing Helpers

    def _find_config_property(self, properties: list) -> dict | None:
        """Find configuration property in properties list."""
        if not properties:
            return None

        for config_name in CONFIG_PROPERTY_NAMES:
            for prop in properties:
                if not isinstance(prop, dict):
                    continue
                if prop.get(KEY_NAME) == config_name and isinstance(
                    prop.get(KEY_VALUE), (bytes, str, dict)
                ):
                    return prop
        return None

    def _get_preferred_device_name(self, node_id: str) -> str | None:
        """Get preferred device name from config entry."""
        entry_id = self.registry.get_config_entry_id(node_id)
        if entry_id:
            config_entry = self.hass.config_entries.async_get_entry(entry_id)
            if config_entry and config_entry.title:
                return config_entry.title
        return None

    def _parse_device_config(
        self,
        config_property: dict | None,
        preferred_device_name: str | None,
    ) -> dict | None:
        """Parse device configuration from config property."""
        if not config_property:
            _LOGGER.warning("No configuration property found")
            return None

        config_value = config_property.get(KEY_VALUE)
        if config_value is None:
            return None

        # Pass config_value directly to parser - it handles both dict and str/bytes
        return self._device_parser.parse_device_config(
            config_value, preferred_device_name
        )
