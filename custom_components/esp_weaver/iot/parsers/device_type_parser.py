# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device type parsers."""

import logging
from typing import Any

from ..specs.binary_sensor_specs import (
    DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
    DEFAULT_DEBOUNCE_TIME_MS,
    DEFAULT_REPORT_INTERVAL_MS,
)
from ..specs.device_specs import DEVICE_TYPE_TEMPERATURE_SENSOR
from ..specs.keys import (
    KEY_ATTRIBUTES,
    KEY_BOUNDS,
    KEY_BRIGHTNESS,
    KEY_CONFIG,
    KEY_DATA_TYPE,
    KEY_DEBOUNCE_TIME,
    KEY_DEVICE_CLASS,
    KEY_DEVICE_INFO,
    KEY_DEVICE_NAME,
    KEY_DISPLAY_NAME,
    KEY_ENTITIES,
    KEY_ENTITY_NAME,
    KEY_ENTITY_TYPE,
    KEY_FRIENDLY_NAME,
    KEY_HUE,
    KEY_ICON,
    KEY_INITIAL_VALUES,
    KEY_INTENSITY,
    KEY_LIGHT_MODE,
    KEY_MAX,
    KEY_MAX_VALUE,
    KEY_MIN,
    KEY_MIN_VALUE,
    KEY_NAME,
    KEY_PARAM,
    KEY_PARAM_NAME,
    KEY_PARAM_TYPE,
    KEY_PARAMS,
    KEY_PLATFORM,
    KEY_PLATFORMS,
    KEY_POWER,
    KEY_PROPERTIES,
    KEY_REPORT_INTERVAL,
    KEY_SATURATION,
    KEY_SENSOR_NAME,
    KEY_SENSOR_TYPE,
    KEY_STATE,
    KEY_STEP,
    KEY_TYPE,
    KEY_UNIT_OF_MEASUREMENT,
    KEY_VALUE,
)
from ..specs.light_specs import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_HUE,
    DEFAULT_INTENSITY,
    DEFAULT_LIGHT_MODE,
    DEFAULT_SATURATION,
)
from ..specs.sensor_specs import get_sensor_definition

# Number entity param type prefix (esp.param.config.xxx)
NUMBER_PARAM_TYPE_PREFIX = "esp.param.config."

# Default icon for number entities
DEFAULT_NUMBER_ICON = "mdi:tune"

_LOGGER = logging.getLogger(__name__)


def _add_entity_to_result(
    result: dict[str, Any],
    platform: str,
    entity_info: dict[str, Any],
) -> None:
    """Add entity info to result platforms and entities lists.

    Args:
        result: Result dictionary to populate.
        platform: Platform name (e.g., 'light', 'sensor').
        entity_info: Entity information dictionary.
    """
    if platform not in result[KEY_PLATFORMS]:
        result[KEY_PLATFORMS][platform] = []
    result[KEY_PLATFORMS][platform].append(entity_info)
    result[KEY_ENTITIES].append(entity_info)


def parse_light_device(device: dict[str, Any], result: dict[str, Any]) -> None:
    """Process light device and extract light parameters.

    Args:
        device: Light device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    light_values: dict[str, bool | int] = {
        KEY_POWER: False,
        KEY_BRIGHTNESS: DEFAULT_BRIGHTNESS,
        KEY_HUE: DEFAULT_HUE,
        KEY_SATURATION: DEFAULT_SATURATION,
        KEY_INTENSITY: DEFAULT_INTENSITY,
        KEY_LIGHT_MODE: DEFAULT_LIGHT_MODE,
    }

    # Mapping: param_name -> (value_key, converter)
    light_param_map: dict[str, tuple[str, type]] = {
        KEY_POWER: (KEY_POWER, bool),
        KEY_BRIGHTNESS: (KEY_BRIGHTNESS, int),
        KEY_HUE: (KEY_HUE, int),
        KEY_SATURATION: (KEY_SATURATION, int),
        KEY_INTENSITY: (KEY_INTENSITY, int),
        KEY_LIGHT_MODE: (KEY_LIGHT_MODE, int),
    }

    for param in params:
        param_name = param.get(KEY_NAME, "")
        properties = param.get(KEY_PROPERTIES, [])
        param_value = param.get(KEY_VALUE)

        param_name_lower = param_name.lower()
        if param_name_lower in light_param_map and "write" in properties:
            value_key, converter = light_param_map[param_name_lower]
            if param_value is not None:
                try:
                    light_values[value_key] = converter(param_value)
                except (ValueError, TypeError):
                    _LOGGER.debug(
                        "Failed to convert %s value '%s' to %s",
                        value_key,
                        param_value,
                        converter.__name__,
                    )

    # Create light entity info
    entity_info = {
        KEY_PLATFORM: "light",
        KEY_DEVICE_NAME: main_device_name,
        KEY_ENTITY_TYPE: "light",
        KEY_INITIAL_VALUES: light_values,
        KEY_PARAMS: params,
    }

    _add_entity_to_result(result, "light", entity_info)


def parse_binary_sensor_device(device: dict[str, Any], result: dict[str, Any]) -> None:
    """Process binary sensor device and extract parameters.

    Creates a binary sensor entity with configuration for state,
    device class, debounce time, and reporting interval.

    Args:
        device: Binary sensor device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    # Extract binary sensor capabilities and config
    binary_sensor_config: dict[str, str] = {
        KEY_NAME: main_device_name,
        KEY_DEVICE_CLASS: DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
    }

    # Extract current parameter values and configuration using constants
    binary_sensor_values: dict[str, bool | str | int] = {
        KEY_STATE: False,
        KEY_DEVICE_CLASS: DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
        KEY_DEBOUNCE_TIME: DEFAULT_DEBOUNCE_TIME_MS,
        KEY_REPORT_INTERVAL: DEFAULT_REPORT_INTERVAL_MS,
    }

    # Process all parameters
    for param in params:
        param_type = param.get(KEY_TYPE, "")
        param_value = param.get(KEY_VALUE)

        if param_type == "esp.param.state":
            binary_sensor_values[KEY_STATE] = (
                bool(param_value) if param_value is not None else False
            )
        elif param_type == "esp.param.device_class":
            device_class = (
                str(param_value).lower()
                if param_value
                else DEFAULT_BINARY_SENSOR_DEVICE_CLASS
            )
            binary_sensor_values[KEY_DEVICE_CLASS] = device_class
            binary_sensor_config[KEY_DEVICE_CLASS] = device_class
        elif param_type == "esp.param.debounce_time":
            try:
                binary_sensor_values[KEY_DEBOUNCE_TIME] = int(param_value)
            except (ValueError, TypeError):
                binary_sensor_values[KEY_DEBOUNCE_TIME] = DEFAULT_DEBOUNCE_TIME_MS
        elif param_type == "esp.param.report_interval":
            try:
                binary_sensor_values[KEY_REPORT_INTERVAL] = int(param_value)
            except (ValueError, TypeError):
                binary_sensor_values[KEY_REPORT_INTERVAL] = DEFAULT_REPORT_INTERVAL_MS

    # Create binary sensor entity info with complete configuration
    entity_info: dict[str, Any] = {
        KEY_PLATFORM: "binary_sensor",
        KEY_DEVICE_NAME: main_device_name,
        KEY_ENTITY_TYPE: "binary_sensor",
        KEY_CONFIG: binary_sensor_config,
        KEY_STATE: binary_sensor_values[KEY_STATE],  # Current state
        KEY_DEVICE_CLASS: binary_sensor_values[KEY_DEVICE_CLASS],  # Device type
        KEY_INITIAL_VALUES: binary_sensor_values,
        KEY_PARAMS: params,
        KEY_ATTRIBUTES: {  # Additional attributes
            KEY_DEBOUNCE_TIME: binary_sensor_values[KEY_DEBOUNCE_TIME],
            KEY_REPORT_INTERVAL: binary_sensor_values[KEY_REPORT_INTERVAL],
            KEY_FRIENDLY_NAME: main_device_name,
        },
    }

    _add_entity_to_result(result, "binary_sensor", entity_info)


def parse_imu_gesture_device(device: dict[str, Any], result: dict[str, Any]) -> None:
    """Process IMU gesture device as a unified gesture controller.

    Args:
        device: IMU gesture device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    gesture_controller_info: dict[str, Any] = {
        KEY_PLATFORM: "imu_gesture",
        KEY_DEVICE_NAME: main_device_name,
        KEY_ENTITY_TYPE: "imu_gesture_controller",
        KEY_ENTITY_NAME: "imu_gesture_controller",
        KEY_PARAMS: params,
    }

    _add_entity_to_result(result, "imu_gesture", gesture_controller_info)


def parse_interactive_input_device(
    device: dict[str, Any], result: dict[str, Any]
) -> None:
    """Process Interactive Input device as a unified input controller.

    Args:
        device: Interactive input device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    input_controller_info: dict[str, Any] = {
        KEY_PLATFORM: "interactive_input",
        KEY_DEVICE_NAME: main_device_name,
        KEY_ENTITY_TYPE: "interactive_input_controller",
        KEY_ENTITY_NAME: "interactive_input_controller",
        KEY_PARAMS: params,
    }

    _add_entity_to_result(result, "interactive_input", input_controller_info)


def parse_battery_energy_device(device: dict[str, Any], result: dict[str, Any]) -> None:
    """Process Battery & Energy device as a unified battery controller.

    Args:
        device: Battery & energy device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    battery_controller_info: dict[str, Any] = {
        KEY_PLATFORM: "battery_energy",
        KEY_DEVICE_NAME: main_device_name,
        KEY_ENTITY_TYPE: "battery_energy_controller",
        KEY_ENTITY_NAME: "battery_energy_controller",
        KEY_PARAMS: params,
    }

    _add_entity_to_result(result, "battery_energy", battery_controller_info)


def parse_low_power_sleep_device(
    device: dict[str, Any], result: dict[str, Any]
) -> None:
    """Process Low Power & Sleep device as a unified power management controller.

    Args:
        device: Low power & sleep device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    sleep_controller_info: dict[str, Any] = {
        KEY_PLATFORM: "low_power_sleep",
        KEY_DEVICE_NAME: main_device_name,
        KEY_ENTITY_TYPE: "low_power_sleep_controller",
        KEY_ENTITY_NAME: "low_power_sleep_controller",
        KEY_PARAMS: params,
    }

    _add_entity_to_result(result, "low_power_sleep", sleep_controller_info)


def _process_number_params(device: dict[str, Any], result: dict[str, Any]) -> None:
    """Process config parameters as number entities.

    Scans device parameters for esp.param.config.xxx types and creates
    number entities for them (e.g., thresholds, update_interval).

    Args:
        device: Device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    for param in params:
        param_type = param.get(KEY_TYPE, "")

        # Only process config parameters
        if not param_type.startswith(NUMBER_PARAM_TYPE_PREFIX):
            continue

        param_name = param.get(KEY_NAME, "")
        if not param_name:
            continue

        data_type = param.get(KEY_DATA_TYPE, "")
        properties = param.get(KEY_PROPERTIES, [])
        bounds = param.get(KEY_BOUNDS, {})

        # Config params should be numeric and writable
        if data_type not in ["int", "float"]:
            continue

        if "write" not in properties:
            continue

        # Generate display name from param_name
        display_name = param_name.replace("_", " ").title()

        number_entity_info: dict[str, Any] = {
            KEY_PLATFORM: "number",
            KEY_DEVICE_NAME: main_device_name,
            KEY_ENTITY_TYPE: "config",
            KEY_ENTITY_NAME: param_name.lower().replace(" ", "_"),
            KEY_DISPLAY_NAME: display_name,
            KEY_ICON: DEFAULT_NUMBER_ICON,
            KEY_PARAM_NAME: param_name,
            KEY_PARAM_TYPE: param_type,
            KEY_PARAM: param,
            KEY_MIN_VALUE: bounds.get(KEY_MIN),
            KEY_MAX_VALUE: bounds.get(KEY_MAX),
            KEY_STEP: bounds.get(KEY_STEP),
        }

        _add_entity_to_result(result, "number", number_entity_info)


def parse_sensor_device(device: dict[str, Any], result: dict[str, Any]) -> None:
    """Process sensor device and create sensor/number entity specifications.

    Processes both sensor reading parameters and config parameters (thresholds).

    Args:
        device: Sensor device configuration dictionary.
        result: Result dictionary to populate with entity info.
    """
    main_device_name = result[KEY_DEVICE_INFO][KEY_NAME]
    params = device.get(KEY_PARAMS, [])

    for param in params:
        param_type = param.get(KEY_TYPE, "")

        # Skip config parameters - handled by _process_number_params below
        if param_type.startswith(NUMBER_PARAM_TYPE_PREFIX):
            continue

        param_name = param.get(KEY_NAME, "")
        param_name_lower = param_name.lower().replace(" ", "_")
        data_type = param.get(KEY_DATA_TYPE, "")
        properties = param.get(KEY_PROPERTIES, [])

        # Only process numeric parameters with read property
        if data_type not in ["int", "float"]:
            continue

        if "read" not in properties:
            continue

        # Get sensor definition for proper unit, device class, icon
        sensor_def = get_sensor_definition(param_name_lower)
        if not sensor_def:
            _LOGGER.debug("No sensor definition found for: %s", param_name_lower)
            continue

        entity_info = {
            KEY_PLATFORM: "sensor",
            KEY_DEVICE_NAME: main_device_name,
            KEY_ENTITY_TYPE: DEVICE_TYPE_TEMPERATURE_SENSOR,
            KEY_SENSOR_TYPE: param_name_lower,
            KEY_SENSOR_NAME: f"{main_device_name} {sensor_def.display_name}",
            KEY_UNIT_OF_MEASUREMENT: sensor_def.unit,
            KEY_DEVICE_CLASS: sensor_def.device_class,
            KEY_ICON: sensor_def.icon,
            KEY_PARAM: param,
        }

        _add_entity_to_result(result, "sensor", entity_info)

    # Process config parameters (thresholds, update_interval, etc.) as number entities
    _process_number_params(device, result)
