# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device configuration parser."""

import json
import logging
from typing import Any

from ..specs.device_specs import (
    DEFAULT_DEVICE_NAME,
    DEFAULT_DEVICE_NAME_PREFIX,
    DEFAULT_MANUFACTURER,
    DEFAULT_MODEL,
    DEVICE_TYPE_MAPPING,
)
from ..specs.keys import (
    KEY_DEVICE_INFO,
    KEY_DEVICES,
    KEY_ENTITIES,
    KEY_FW_VERSION,
    KEY_HW_VERSION,
    KEY_IDENTIFIERS,
    KEY_INFO,
    KEY_MANUFACTURER,
    KEY_MODEL,
    KEY_NAME,
    KEY_NODE_ID,
    KEY_PLATFORM,
    KEY_PLATFORMS,
    KEY_PROJECT_NAME,
    KEY_SW_VERSION,
    KEY_TYPE,
)
from .device_type_parser import (
    parse_battery_energy_device,
    parse_binary_sensor_device,
    parse_imu_gesture_device,
    parse_interactive_input_device,
    parse_light_device,
    parse_low_power_sleep_device,
    parse_sensor_device,
)

_LOGGER = logging.getLogger(__name__)


class ESPDeviceParser:
    """Parse ESP-RainMaker device configuration and determine required entities.

    This parser converts ESP-RainMaker device configurations into Home Assistant
    entity specifications. It handles various device types including:
    - Lights (with brightness, color, temperature control)
    - Sensors (temperature, humidity, pressure, etc.)
    - Binary sensors
    - IMU gesture devices
    - Interactive input devices
    - Battery and energy monitoring devices
    - Low power and sleep management devices

    The parser maintains mappings between ESP-RainMaker device/parameter types
    and their corresponding Home Assistant platforms and entity types.
    """

    def __init__(self, domain: str) -> None:
        """Initialize the ESP device parser.

        Args:
            domain: Integration domain name for device identifiers.
        """
        self._domain = domain

        # Map device types to processor functions
        self._device_parsers = {
            "light": parse_light_device,
            "sensor": parse_sensor_device,
            "binary_sensor": parse_binary_sensor_device,
            "imu_gesture": parse_imu_gesture_device,
            "interactive_input": parse_interactive_input_device,
            "battery_energy": parse_battery_energy_device,
            "low_power_sleep": parse_low_power_sleep_device,
        }

    def parse_device_config(
        self,
        config_data: bytes | str | dict[str, Any] | None,
        preferred_name: str | None = None,
    ) -> dict[str, Any]:
        """Parse ESP device configuration from protobuf response.

        Converts raw configuration data (bytes, string, or dict) into a structured
        dictionary containing device information and entity specifications
        for Home Assistant platforms.

        Args:
            config_data: Raw config data in bytes, string, dict, or None format.
                Expected to be JSON-formatted device config or already parsed dict.
            preferred_name: Optional preferred device name (from config entry).
                If provided, this overrides the name from ESP device config.

        Returns:
            Dictionary containing:
                - device_info: Device metadata (name, model, manufacturer, etc.)
                - platforms: Entity specifications grouped by platform type
                - entities: List of all entity specifications
            Returns empty dict if parsing fails (errors are logged, not raised).
        """
        if config_data is None:
            _LOGGER.error("No configuration data received (None)")
            return {}

        try:
            # Handle dict input directly (already parsed)
            if isinstance(config_data, dict):
                return self._extract_entity_info(config_data, preferred_name)

            # Handle both bytes and string input
            # Use strict decoding to detect corrupted data early rather than
            # silently replacing invalid bytes which could corrupt JSON structure
            config_str = (
                config_data.decode("utf-8")
                if isinstance(config_data, bytes)
                else str(config_data)
            )

            # Parse and validate JSON configuration
            device_config = json.loads(config_str)

            return self._extract_entity_info(device_config, preferred_name)

        except json.JSONDecodeError as json_err:
            _LOGGER.error(
                "Failed to parse device configuration JSON: %s", str(json_err)
            )
            return {}

        except UnicodeDecodeError as decode_err:
            _LOGGER.error(
                "Failed to decode device configuration string: %s", str(decode_err)
            )
            return {}

        except (KeyError, TypeError, ValueError):
            # KeyError: missing required keys in config
            # TypeError: wrong types in config
            # ValueError: invalid values in config
            _LOGGER.exception("Unexpected error processing device configuration")
            return {}

        except Exception:  # pylint: disable=broad-exception-caught
            # Catch-all to ensure parser always returns {} for any unexpected error
            _LOGGER.exception("Unexpected error parsing device configuration")
            return {}

    def _extract_entity_info(
        self, config: dict[str, Any], preferred_name: str | None = None
    ) -> dict[str, Any]:
        """Extract entity information from device configuration.

        Processes the device configuration to extract device metadata
        and create entity specifications for all supported platforms.

        Args:
            config: Device configuration dictionary with 'info', 'devices',
                and 'services' sections.
            preferred_name: Optional preferred device name.

        Returns:
            Dictionary with device_info, platforms, and entities.
        """
        result: dict[str, Any] = {
            KEY_DEVICE_INFO: self._extract_device_info(config, preferred_name),
            KEY_PLATFORMS: {},  # Platform type -> entities
            KEY_ENTITIES: [],  # All entities list
        }

        # Process devices section
        devices = config.get(KEY_DEVICES, [])
        for device in devices:
            self._process_device(device, result)

        return result

    def _extract_device_info(
        self, config: dict[str, Any], preferred_name: str | None = None
    ) -> dict[str, Any]:
        """Extract device information with unified naming.

        Creates a standardized device info dictionary that ensures all
        entities for this device are grouped together in Home Assistant.

        Args:
            config: Device configuration dictionary.
            preferred_name: Optional preferred device name.

        Returns:
            Device info dictionary with identifiers, name, manufacturer, etc.
        """
        info = config.get(KEY_INFO, {})
        node_id = config.get(KEY_NODE_ID, "")

        # Use preferred name if provided, otherwise use device's name
        if preferred_name:
            device_name = preferred_name
        else:
            device_name = info.get(
                KEY_NAME,
                f"{DEFAULT_DEVICE_NAME_PREFIX}{node_id}"
                if node_id
                else DEFAULT_DEVICE_NAME,
            )

        # Create standardized device info to ensure all entities belong to same device
        return {
            KEY_IDENTIFIERS: {(self._domain, node_id)},
            KEY_NAME: device_name,  # This is the main device name all entities will use
            KEY_MANUFACTURER: DEFAULT_MANUFACTURER,
            KEY_MODEL: info.get(KEY_MODEL, DEFAULT_MODEL),
            KEY_HW_VERSION: info.get(KEY_HW_VERSION, ""),
            KEY_SW_VERSION: info.get(KEY_FW_VERSION, ""),
            KEY_NODE_ID: node_id,
            KEY_TYPE: info.get(KEY_TYPE, ""),
            KEY_PLATFORM: info.get(KEY_PLATFORM, ""),
            KEY_PROJECT_NAME: info.get(KEY_PROJECT_NAME, ""),
        }

    def _process_device(self, device: dict[str, Any], result: dict[str, Any]) -> None:
        """Process a device and its parameters.

        Routes device processing to the appropriate handler based on
        the device type mapping.

        Args:
            device: Device configuration dictionary.
            result: Result dictionary to populate with entity info.
        """
        device_type = device.get(KEY_TYPE, "")
        ha_platform = DEVICE_TYPE_MAPPING.get(device_type)

        if ha_platform and ha_platform in self._device_parsers:
            parser = self._device_parsers[ha_platform]
            parser(device, result)
        else:
            _LOGGER.debug(
                "Skipping unsupported device type '%s' (HA platform: %s)",
                device_type,
                ha_platform or "unknown",
            )
