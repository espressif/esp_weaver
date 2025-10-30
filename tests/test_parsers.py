# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver parsers module."""

import json
from unittest.mock import patch

import pytest

from custom_components.esp_weaver.iot.parsers.config_parser import ESPDeviceParser
from custom_components.esp_weaver.iot.parsers.property_parser import (
    ParsedProperties,
    decode_property_value,
    parse_device_properties,
)
from custom_components.esp_weaver.iot.utils.sensor_utils import (
    get_sensor_threshold_config,
)


class TestDecodePropertyValue:
    """Test decode_property_value function."""

    def test_decode_none(self) -> None:
        """Test decoding None value."""
        result = decode_property_value(None)
        assert result is None

    def test_decode_dict(self) -> None:
        """Test decoding dict value."""
        data = {"key": "value", "number": 42}
        result = decode_property_value(data)
        assert result == data

    def test_decode_bytes_utf8(self) -> None:
        """Test decoding UTF-8 bytes."""
        data = {"temperature": 25.5}
        bytes_data = json.dumps(data).encode("utf-8")

        result = decode_property_value(bytes_data)

        assert result == data

    def test_decode_bytes_latin1(self) -> None:
        """Test decoding Latin-1 bytes that fail UTF-8."""
        # 0xE9 is "é" in Latin-1 but invalid as standalone byte in UTF-8
        bytes_data = b'{"caf\xe9": "value"}'

        result = decode_property_value(bytes_data)

        assert result == {"café": "value"}

    @pytest.mark.parametrize(
        "input_data",
        [
            b"not json data",
            b'{"incomplete": ',
            "not json",
            '{"incomplete":',
        ],
        ids=[
            "bytes_non_json",
            "bytes_invalid_json",
            "string_non_json",
            "string_invalid_json",
        ],
    )
    def test_decode_invalid_inputs(self, input_data: bytes | str) -> None:
        """Test decoding various invalid inputs (non-JSON and invalid JSON)."""
        result = decode_property_value(input_data)
        assert result is None

    def test_decode_string_json(self) -> None:
        """Test decoding JSON string."""
        data = {"sensor": "temperature", "value": 30}
        json_str = json.dumps(data)

        result = decode_property_value(json_str)

        assert result == data


class TestParsedProperties:
    """Test ParsedProperties dataclass."""

    def test_creation(self) -> None:
        """Test ParsedProperties creation."""
        props = ParsedProperties(
            raw_params={"key": "value"},
            by_device_type={"Light": {"power": True}},
        )

        assert props.raw_params == {"key": "value"}
        assert props.by_device_type == {"Light": {"power": True}}

    def test_creation_empty(self) -> None:
        """Test ParsedProperties with empty values."""
        props = ParsedProperties(raw_params=None, by_device_type={})

        assert props.raw_params is None
        assert props.by_device_type == {}


class TestParseDeviceProperties:
    """Test parse_device_properties function."""

    def test_parse_none(self) -> None:
        """Test parsing None properties."""
        result = parse_device_properties(None)

        assert isinstance(result, ParsedProperties)
        assert result.raw_params is None
        assert result.by_device_type == {}

    def test_parse_empty_list(self) -> None:
        """Test parsing empty properties list."""
        result = parse_device_properties([])

        assert result.raw_params is None
        assert result.by_device_type == {}

    def test_parse_with_params(self) -> None:
        """Test parsing properties with params."""
        properties = [
            {
                "name": "params",
                "value": json.dumps({"Light": {"power": True}}),
            }
        ]

        result = parse_device_properties(properties)

        assert result.raw_params == {"Light": {"power": True}}
        assert result.by_device_type == {"Light": {"power": True}}

    def test_parse_with_device_types(self) -> None:
        """Test parsing properties with device types in params format."""
        # Device types should be nested in the JSON value, not as property names
        properties = [
            {
                "name": "params",
                "value": json.dumps(
                    {
                        "Light": {"power": True, "brightness": 128},
                        "Sensor": {"temperature": 25.5},
                    }
                ),
            }
        ]

        result = parse_device_properties(properties)

        assert isinstance(result, ParsedProperties)
        assert result.raw_params == {
            "Light": {"power": True, "brightness": 128},
            "Sensor": {"temperature": 25.5},
        }
        assert result.by_device_type == {
            "Light": {"power": True, "brightness": 128},
            "Sensor": {"temperature": 25.5},
        }

    def test_parse_bytes_values(self) -> None:
        """Test parsing properties with bytes values."""
        properties = [
            {
                "name": "params",
                "value": b'{"Light": {"temperature": 25}}',
            }
        ]

        result = parse_device_properties(properties)

        # Should decode bytes and parse JSON correctly
        assert isinstance(result, ParsedProperties)
        assert result.raw_params == {"Light": {"temperature": 25}}
        assert result.by_device_type == {"Light": {"temperature": 25}}

    def test_parse_invalid_values(self) -> None:
        """Test parsing properties with invalid values."""
        properties = [
            {
                "name": "params",
                "value": "not valid json {",
            }
        ]

        result = parse_device_properties(properties)

        # Invalid JSON should be skipped, resulting in empty values
        assert isinstance(result, ParsedProperties)
        assert result.raw_params is None
        assert result.by_device_type == {}

    def test_parse_mixed_valid_invalid(self) -> None:
        """Test parsing mix of valid and invalid properties."""
        properties = [
            {
                "name": "params",
                "value": json.dumps({"Light": {"power": True}}),
            },
            {
                "name": "invalid",
                "value": "not json",
            },
        ]

        result = parse_device_properties(properties)

        # Valid property should be parsed, invalid should be skipped
        assert isinstance(result, ParsedProperties)
        assert result.raw_params == {"Light": {"power": True}}
        assert result.by_device_type == {"Light": {"power": True}}


class TestParseDevicePropertiesEdgeCases:
    """Test edge cases for parse_device_properties."""

    def test_empty_property_value(self) -> None:
        """Test property with empty value."""
        properties = [{"name": "test", "value": ""}]

        result = parse_device_properties(properties)

        # Empty string value should be skipped (doesn't start with "{")
        assert isinstance(result, ParsedProperties)
        assert result.raw_params is None
        assert result.by_device_type == {}

    def test_property_without_value_key(self) -> None:
        """Test property without value key."""
        properties = [{"name": "test"}]

        result = parse_device_properties(properties)

        # Missing value key should be handled gracefully (skipped)
        assert isinstance(result, ParsedProperties)
        assert result.raw_params is None
        assert result.by_device_type == {}

    def test_property_without_name_key(self) -> None:
        """Test property without name key."""
        properties = [{"value": '{"Light": {"power": true}}'}]

        result = parse_device_properties(properties)

        # Missing name key defaults to empty string, treated as params
        assert isinstance(result, ParsedProperties)
        # raw_params is captured because empty name is treated as params
        assert result.raw_params == {"Light": {"power": True}}
        # by_device_type is also populated from the decoded value
        assert result.by_device_type == {"Light": {"power": True}}

    def test_nested_json_values(self) -> None:
        """Test deeply nested JSON values."""
        nested_data = {
            "level1": {
                "level2": {
                    "level3": {"value": 123},
                }
            }
        }
        properties = [
            {
                "name": "params",
                "value": json.dumps(nested_data),
            }
        ]

        result = parse_device_properties(properties)

        assert isinstance(result, ParsedProperties)
        # raw_params preserves the nested structure
        assert result.raw_params == nested_data
        # by_device_type organizes by top-level keys (device types)
        assert result.by_device_type == {
            "level1": {"level2": {"level3": {"value": 123}}}
        }


# =============================================================================
# Config Parser Tests
# =============================================================================
class TestConfigParserEdgeCases:
    """Test ESPDeviceParser edge cases."""

    def test_parse_device_config_none(self) -> None:
        """Test parsing None config returns empty dict."""
        parser = ESPDeviceParser(domain="esp_weaver")
        result = parser.parse_device_config(None)

        assert result == {}

    def test_parse_device_config_invalid_json(self) -> None:
        """Test parsing invalid JSON string returns empty dict."""
        parser = ESPDeviceParser(domain="esp_weaver")
        result = parser.parse_device_config("not valid json {")

        assert result == {}

    def test_parse_device_config_invalid_bytes(self) -> None:
        """Test parsing invalid UTF-8 bytes returns empty dict."""
        parser = ESPDeviceParser(domain="esp_weaver")
        # Invalid UTF-8 sequence that can't be decoded with strict mode
        result = parser.parse_device_config(b"\xff\xfe invalid")

        assert result == {}

    def test_parse_device_config_key_error(self) -> None:
        """Test parsing config with missing keys returns empty dict."""
        parser = ESPDeviceParser(domain="esp_weaver")

        # Mock _extract_entity_info to raise KeyError
        with patch.object(
            parser, "_extract_entity_info", side_effect=KeyError("missing_key")
        ):
            result = parser.parse_device_config('{"valid": "json"}')

        assert result == {}

    def test_parse_device_config_type_error(self) -> None:
        """Test parsing config with type error returns empty dict."""
        parser = ESPDeviceParser(domain="esp_weaver")

        # Mock _extract_entity_info to raise TypeError
        with patch.object(
            parser, "_extract_entity_info", side_effect=TypeError("wrong type")
        ):
            result = parser.parse_device_config('{"valid": "json"}')

        assert result == {}

    def test_parse_device_config_unexpected_error(self) -> None:
        """Test parsing config with unexpected error returns empty dict."""
        parser = ESPDeviceParser(domain="esp_weaver")

        # Mock _extract_entity_info to raise unexpected exception
        with patch.object(
            parser, "_extract_entity_info", side_effect=RuntimeError("unexpected")
        ):
            result = parser.parse_device_config('{"valid": "json"}')

        assert result == {}


# =============================================================================
# Sensor Utils Tests
# =============================================================================
class TestSensorUtilsEdgeCases:
    """Test sensor_utils edge cases."""

    def test_get_sensor_threshold_config_unknown_sensor(self) -> None:
        """Test get_sensor_threshold_config with unknown sensor type."""
        result = get_sensor_threshold_config("unknown_sensor_xyz")
        assert result is None
