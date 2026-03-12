# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the device_type_parser module."""

from custom_components.esp_weaver.iot.parsers.device_type_parser import (
    parse_battery_energy_device,
    parse_binary_sensor_device,
    parse_imu_gesture_device,
    parse_interactive_input_device,
    parse_light_device,
    parse_low_power_sleep_device,
    parse_sensor_device,
)


def create_base_result(device_name: str = "Test Device") -> dict:
    """Create base result dictionary for tests."""
    return {
        "device_info": {"name": device_name},
        "platforms": {},
        "entities": [],
    }


class TestParseLightDevice:
    """Test parse_light_device function."""

    def test_basic_light(self) -> None:
        """Test parsing basic light device."""
        device = {
            "name": "Light",
            "params": [
                {"name": "power", "properties": ["read", "write"], "value": True},
            ],
        }
        result = create_base_result()

        parse_light_device(device, result)

        assert "light" in result["platforms"]
        assert len(result["platforms"]["light"]) == 1
        entity = result["platforms"]["light"][0]
        assert entity["platform"] == "light"
        # Check initial_values instead of capabilities
        assert entity["initial_values"]["power"] is True

    def test_full_color_light(self) -> None:
        """Test parsing full color light with all values."""
        device = {
            "name": "Color Light",
            "params": [
                {"name": "power", "properties": ["read", "write"], "value": True},
                {"name": "brightness", "properties": ["read", "write"], "value": 100},
                {"name": "hue", "properties": ["read", "write"], "value": 180},
                {"name": "saturation", "properties": ["read", "write"], "value": 50},
                {"name": "intensity", "properties": ["read", "write"], "value": 75},
            ],
        }
        result = create_base_result()

        parse_light_device(device, result)

        entity = result["platforms"]["light"][0]
        # Check initial_values instead of capabilities
        assert entity["initial_values"]["power"] is True
        assert entity["initial_values"]["brightness"] == 100
        assert entity["initial_values"]["hue"] == 180
        assert entity["initial_values"]["saturation"] == 50
        assert entity["initial_values"]["intensity"] == 75

    def test_read_only_params_not_updated(self) -> None:
        """Test read-only params use default values."""
        device = {
            "name": "Light",
            "params": [
                {"name": "power", "properties": ["read"], "value": True},  # No write
                {"name": "brightness", "properties": ["read", "write"], "value": 80},
            ],
        }
        result = create_base_result()

        parse_light_device(device, result)

        entity = result["platforms"]["light"][0]
        # Read-only params don't get updated, so power stays default (False)
        assert entity["initial_values"]["power"] is False
        # Writable params get their values
        assert entity["initial_values"]["brightness"] == 80

    def test_empty_params(self) -> None:
        """Test light device with no params uses defaults."""
        device = {"name": "Light", "params": []}
        result = create_base_result()

        parse_light_device(device, result)

        entity = result["platforms"]["light"][0]
        # Empty params means all defaults
        assert entity["initial_values"]["power"] is False
        # Check params list is empty
        assert entity["params"] == []


class TestParseBinarySensorDevice:
    """Test parse_binary_sensor_device function."""

    def test_basic_binary_sensor(self) -> None:
        """Test parsing basic binary sensor."""
        device = {
            "name": "Motion Sensor",
            "params": [
                {"name": "state", "type": "esp.param.state", "value": True},
                {
                    "name": "device_class",
                    "type": "esp.param.device_class",
                    "value": "motion",
                },
            ],
        }
        result = create_base_result()

        parse_binary_sensor_device(device, result)

        assert "binary_sensor" in result["platforms"]
        entity = result["platforms"]["binary_sensor"][0]
        assert entity["platform"] == "binary_sensor"
        assert entity["state"] is True
        assert entity["device_class"] == "motion"

    def test_binary_sensor_defaults(self) -> None:
        """Test binary sensor default values."""
        device = {"name": "Sensor", "params": []}
        result = create_base_result()

        parse_binary_sensor_device(device, result)

        entity = result["platforms"]["binary_sensor"][0]
        assert entity["state"] is False
        assert entity["initial_values"]["debounce_time"] == 100
        assert entity["initial_values"]["report_interval"] == 1000

    def test_binary_sensor_with_config(self) -> None:
        """Test binary sensor with custom config."""
        device = {
            "name": "Door Sensor",
            "params": [
                {"name": "state", "type": "esp.param.state", "value": False},
                {
                    "name": "debounce_time",
                    "type": "esp.param.debounce_time",
                    "value": 200,
                },
                {
                    "name": "report_interval",
                    "type": "esp.param.report_interval",
                    "value": 2000,
                },
            ],
        }
        result = create_base_result()

        parse_binary_sensor_device(device, result)

        entity = result["platforms"]["binary_sensor"][0]
        assert entity["initial_values"]["debounce_time"] == 200
        assert entity["initial_values"]["report_interval"] == 2000


class TestParseImuGestureDevice:
    """Test parse_imu_gesture_device function."""

    def test_imu_gesture_device(self) -> None:
        """Test parsing IMU gesture device."""
        device = {
            "name": "IMU Gesture",
            "params": [
                {"name": "gesture_type", "type": "esp.param.gesture_type"},
                {"name": "sensitivity", "type": "esp.param.sensitivity"},
            ],
        }
        result = create_base_result()

        parse_imu_gesture_device(device, result)

        assert "imu_gesture" in result["platforms"]
        entity = result["platforms"]["imu_gesture"][0]
        assert entity["platform"] == "imu_gesture"
        assert entity["entity_type"] == "imu_gesture_controller"
        # Verify params are stored
        assert len(entity["params"]) == 2


class TestParseInteractiveInputDevice:
    """Test parse_interactive_input_device function."""

    def test_interactive_input_device(self) -> None:
        """Test parsing interactive input device."""
        device = {
            "name": "Interactive Input",
            "params": [
                {"name": "input_type", "type": "esp.param.input_type"},
            ],
        }
        result = create_base_result()

        parse_interactive_input_device(device, result)

        assert "interactive_input" in result["platforms"]
        entity = result["platforms"]["interactive_input"][0]
        assert entity["platform"] == "interactive_input"
        assert entity["entity_type"] == "interactive_input_controller"
        # Verify params are stored
        assert len(entity["params"]) == 1


class TestParseBatteryEnergyDevice:
    """Test parse_battery_energy_device function."""

    def test_battery_energy_device(self) -> None:
        """Test parsing battery & energy device."""
        device = {
            "name": "Battery & Energy",
            "params": [
                {"name": "battery_level", "type": "esp.param.battery_level"},
                {"name": "voltage", "type": "esp.param.voltage"},
            ],
        }
        result = create_base_result()

        parse_battery_energy_device(device, result)

        assert "battery_energy" in result["platforms"]
        entity = result["platforms"]["battery_energy"][0]
        assert entity["platform"] == "battery_energy"
        assert entity["entity_type"] == "battery_energy_controller"
        # Verify params are stored
        assert len(entity["params"]) == 2


class TestParseLowPowerSleepDevice:
    """Test parse_low_power_sleep_device function."""

    def test_low_power_sleep_device(self) -> None:
        """Test parsing low power & sleep device."""
        device = {
            "name": "Low Power & Sleep",
            "params": [
                {"name": "sleep_state", "type": "esp.param.sleep_state"},
            ],
        }
        result = create_base_result()

        parse_low_power_sleep_device(device, result)

        assert "low_power_sleep" in result["platforms"]
        entity = result["platforms"]["low_power_sleep"][0]
        assert entity["platform"] == "low_power_sleep"
        assert entity["entity_type"] == "low_power_sleep_controller"
        # Verify params are stored
        assert len(entity["params"]) == 1


class TestParseSensorDevice:
    """Test parse_sensor_device function."""

    def test_temperature_sensor(self) -> None:
        """Test parsing temperature sensor."""
        device = {
            "name": "Temperature Sensor",
            "params": [
                {
                    "name": "temperature",
                    "type": "esp.param.temperature",
                    "data_type": "float",
                    "properties": ["read"],
                },
            ],
        }
        result = create_base_result()

        parse_sensor_device(device, result)

        assert "sensor" in result["platforms"]
        entity = result["platforms"]["sensor"][0]
        assert entity["platform"] == "sensor"
        assert entity["sensor_type"] == "temperature"

    def test_multiple_sensors(self) -> None:
        """Test parsing device with multiple sensors."""
        device = {
            "name": "Environment Sensor",
            "params": [
                {
                    "name": "temperature",
                    "type": "esp.param.temperature",
                    "data_type": "float",
                    "properties": ["read"],
                },
                {
                    "name": "humidity",
                    "type": "esp.param.humidity",
                    "data_type": "float",
                    "properties": ["read"],
                },
            ],
        }
        result = create_base_result()

        parse_sensor_device(device, result)

        assert len(result["platforms"]["sensor"]) == 2
        sensor_types = [e["sensor_type"] for e in result["platforms"]["sensor"]]
        assert "temperature" in sensor_types
        assert "humidity" in sensor_types

    def test_config_params_create_number_entities(self) -> None:
        """Test config params create number entities."""
        device = {
            "name": "Temperature Sensor",
            "params": [
                {
                    "name": "temperature",
                    "type": "esp.param.temperature",
                    "data_type": "float",
                    "properties": ["read"],
                },
                {
                    "name": "update_interval",
                    "type": "esp.param.config.update_interval",
                    "data_type": "int",
                    "properties": ["read", "write"],
                    "bounds": {"min": 1, "max": 3600},
                },
            ],
        }
        result = create_base_result()

        parse_sensor_device(device, result)

        assert "number" in result["platforms"]
        number_entity = result["platforms"]["number"][0]
        assert number_entity["platform"] == "number"
        assert number_entity["min_value"] == 1
        assert number_entity["max_value"] == 3600

    def test_non_numeric_params_ignored(self) -> None:
        """Test non-numeric params are ignored for sensors."""
        device = {
            "name": "Sensor",
            "params": [
                {
                    "name": "status",
                    "type": "esp.param.status",
                    "data_type": "string",  # Not numeric
                    "properties": ["read"],
                },
            ],
        }
        result = create_base_result()

        parse_sensor_device(device, result)

        assert "sensor" not in result["platforms"]

    def test_read_only_config_params(self) -> None:
        """Test config params without write property are ignored."""
        device = {
            "name": "Sensor",
            "params": [
                {
                    "name": "read_only_config",
                    "type": "esp.param.config.readonly",
                    "data_type": "int",
                    "properties": ["read"],  # No write
                },
            ],
        }
        result = create_base_result()

        parse_sensor_device(device, result)

        assert "number" not in result["platforms"]


class TestMultiplePlatforms:
    """Test parsing devices with multiple platforms."""

    def test_result_structure(self) -> None:
        """Test result structure is correctly populated."""
        device = {
            "name": "Light",
            "params": [{"name": "power", "properties": ["read", "write"]}],
        }
        result = create_base_result("My Device")

        parse_light_device(device, result)

        # Check entity is in both platforms dict and entities list
        assert len(result["platforms"]["light"]) == 1
        assert len(result["entities"]) == 1
        # Use `is` to verify both references point to the exact same object instance,
        # not just objects with equal content. This ensures no duplicate objects are created.
        assert result["entities"][0] is result["platforms"]["light"][0]


class TestErrorHandling:
    """Test error handling and edge cases for device type parsers."""

    def test_missing_name(self) -> None:
        """Test parsing device with missing name field."""
        device = {
            "params": [{"name": "power", "properties": ["read", "write"]}],
        }
        result = create_base_result()

        # Should handle gracefully - parser may use empty/default name
        parse_light_device(device, result)

        # Light entity should still be created
        assert "light" in result["platforms"]

    def test_missing_params(self) -> None:
        """Test parsing device with missing params field handles gracefully."""
        device = {"name": "Light"}
        result = create_base_result()

        # Parser uses .get() with default, so it handles missing params gracefully
        parse_light_device(device, result)

        # Light entity should still be created with default values
        assert "light" in result["platforms"]
        light_entity = result["platforms"]["light"][0]
        # Verify default/empty capabilities
        assert light_entity["initial_values"]["power"] is False

    def test_malformed_param_missing_properties(self) -> None:
        """Test parsing param without properties field - gracefully skips invalid params."""
        device = {
            "name": "Light",
            "params": [
                {"name": "power"},  # Missing properties - should be skipped
            ],
        }
        result = create_base_result()

        # Parser should handle gracefully by skipping params without properties
        parse_light_device(device, result)

        # Light entity should still be created with default values
        assert "light" in result["platforms"]
        # Power should remain at default (False) since param was skipped
        assert result["platforms"]["light"][0]["initial_values"]["power"] is False

    def test_invalid_param_type(self) -> None:
        """Test parsing with invalid param data type."""
        device = {
            "name": "Sensor",
            "params": [
                {
                    "name": "temperature",
                    "type": "esp.param.temperature",
                    "data_type": "invalid_type",  # Invalid data type
                    "properties": ["read"],
                },
            ],
        }
        result = create_base_result()

        # Should handle gracefully - non-numeric types are filtered
        parse_sensor_device(device, result)

        # Sensor with invalid type should be skipped
        assert "sensor" not in result["platforms"]

    def test_empty_device_dict(self) -> None:
        """Test parsing completely empty device handles gracefully."""
        device = {}
        result = create_base_result()

        # Parser uses .get() with defaults, so it handles empty device gracefully
        parse_light_device(device, result)

        # Light entity should still be created with default/empty values
        assert "light" in result["platforms"]
        light_entity = result["platforms"]["light"][0]
        # Verify default values are set (no capabilities from empty device)
        assert light_entity["initial_values"]["power"] is False

    def test_none_param_value(self) -> None:
        """Test parsing param with None value."""
        device = {
            "name": "Sensor",
            "params": [
                {
                    "name": "temperature",
                    "type": "esp.param.temperature",
                    "data_type": "float",
                    "properties": ["read"],
                    "value": None,  # None value
                },
            ],
        }
        result = create_base_result()

        parse_sensor_device(device, result)

        # Should create sensor entity (value can be None initially)
        assert "sensor" in result["platforms"]
