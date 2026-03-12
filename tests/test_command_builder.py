# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the command_builder module."""

from custom_components.esp_weaver.iot.payload.command_builder import (
    build_device_command,
    build_light_command,
    build_threshold_command,
)
from custom_components.esp_weaver.iot.specs.device_specs import (
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_TEMPERATURE_SENSOR,
)


class TestBuildLightCommand:
    """Test build_light_command function."""

    def test_power_on(self) -> None:
        """Test building power on command."""
        result = build_light_command("power", True)

        assert result == {DEVICE_TYPE_LIGHT: {"Power": True}}

    def test_power_off(self) -> None:
        """Test building power off command (with capitalized input)."""
        # Test case-insensitive: "Power" should work same as "power"
        result = build_light_command("Power", False)

        assert result == {DEVICE_TYPE_LIGHT: {"Power": False}}

    def test_brightness(self) -> None:
        """Test building brightness command."""
        result = build_light_command("brightness", 128)

        assert result == {DEVICE_TYPE_LIGHT: {"Brightness": 128}}

    def test_hue(self) -> None:
        """Test building hue command."""
        result = build_light_command("hue", 180)

        assert result == {DEVICE_TYPE_LIGHT: {"Hue": 180}}

    def test_saturation(self) -> None:
        """Test building saturation command."""
        result = build_light_command("saturation", 50)

        assert result == {DEVICE_TYPE_LIGHT: {"Saturation": 50}}

    def test_intensity(self) -> None:
        """Test building intensity command."""
        result = build_light_command("intensity", 75)

        assert result == {DEVICE_TYPE_LIGHT: {"Intensity": 75}}

    def test_unknown_param_passed_through(self) -> None:
        """Test unknown parameter is passed through as-is."""
        result = build_light_command("custom_param", "value")

        assert result == {DEVICE_TYPE_LIGHT: {"custom_param": "value"}}


class TestBuildThresholdCommand:
    """Test build_threshold_command function."""

    def test_temperature_min_threshold(self) -> None:
        """Test building temperature min threshold command."""
        result = build_threshold_command("temperature_min_threshold", 15.0)

        assert result == {
            DEVICE_TYPE_TEMPERATURE_SENSOR: {"temperature_min_threshold": 15.0}
        }

    def test_temperature_max_threshold(self) -> None:
        """Test building temperature max threshold command."""
        result = build_threshold_command("temperature_max_threshold", 30.0)

        assert result == {
            DEVICE_TYPE_TEMPERATURE_SENSOR: {"temperature_max_threshold": 30.0}
        }

    def test_humidity_threshold(self) -> None:
        """Test building humidity threshold command."""
        result = build_threshold_command("humidity_min_threshold", 40)

        assert result == {
            DEVICE_TYPE_TEMPERATURE_SENSOR: {"humidity_min_threshold": 40}
        }


class TestBuildDeviceCommand:
    """Test build_device_command main entry point."""

    def test_light_power(self) -> None:
        """Test routing to light command for power."""
        result = build_device_command("power", True)

        assert result == {DEVICE_TYPE_LIGHT: {"Power": True}}

    def test_light_brightness(self) -> None:
        """Test routing to light command for brightness."""
        result = build_device_command("brightness", 100)

        assert result == {DEVICE_TYPE_LIGHT: {"Brightness": 100}}

    def test_threshold_parameter(self) -> None:
        """Test routing to threshold command."""
        result = build_device_command("temperature_min_threshold", 20.0)

        assert result == {
            DEVICE_TYPE_TEMPERATURE_SENSOR: {"temperature_min_threshold": 20.0}
        }

    def test_unknown_parameter_returns_none(self) -> None:
        """Test unknown parameter returns None."""
        result = build_device_command("completely_unknown_param", "value")

        assert result is None

    def test_case_insensitive(self) -> None:
        """Test parameter matching is case insensitive."""
        result1 = build_device_command("POWER", True)
        result2 = build_device_command("Power", True)
        result3 = build_device_command("power", True)

        assert result1 == result2 == result3


class TestDeviceTypeRegistry:
    """Test device type registry functionality."""

    def test_registry_has_entries(self) -> None:
        """Test registry has default entries.

        Default device types include: light, sensor/threshold.
        """
        # Verify specific types are present by testing known parameters
        assert build_device_command("power", True) is not None
        assert build_device_command("temperature_min_threshold", 20.0) is not None
