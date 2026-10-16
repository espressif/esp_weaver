# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the client_utils module."""

import json
from typing import Any

import pytest

from custom_components.esp_weaver.iot.client.client_utils import (
    convert_values_to_esp_format,
    parse_property_count_response,
    parse_property_values_response,
)


class TestConvertValuesToEspFormat:
    """Test convert_values_to_esp_format function."""

    def test_empty_list(self) -> None:
        """Test converting empty list."""
        result = convert_values_to_esp_format([])

        assert result == []

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("hello", b"hello"),
            (42, b"42"),
            (3.14, b"3.14"),
            (True, b"true"),
            (False, b"false"),
            (None, b"null"),
            ("你好世界", "你好世界".encode()),
        ],
        ids=[
            "string",
            "integer",
            "float",
            "boolean_true",
            "boolean_false",
            "null",
            "unicode",
        ],
    )
    def test_primitive_values(self, input_value: Any, expected: bytes) -> None:
        """Test converting primitive values to ESP format."""
        result = convert_values_to_esp_format([input_value])

        assert result == [expected]

    def test_dict_value(self) -> None:
        """Test converting dict value."""
        data = {"key": "value", "number": 42}
        result = convert_values_to_esp_format([data])

        assert len(result) == 1
        # Parse back to verify it's valid JSON
        parsed = json.loads(result[0].decode("utf-8"))
        assert parsed == data

    def test_nested_dict(self) -> None:
        """Test converting nested dict."""
        data = {"outer": {"inner": {"deep": True}}}
        result = convert_values_to_esp_format([data])

        parsed = json.loads(result[0].decode("utf-8"))
        assert parsed == data

    def test_list_value(self) -> None:
        """Test converting list value."""
        data = [1, 2, 3, "test"]
        result = convert_values_to_esp_format([data])

        parsed = json.loads(result[0].decode("utf-8"))
        assert parsed == data

    def test_multiple_values(self) -> None:
        """Test converting multiple values."""
        result = convert_values_to_esp_format(["hello", 42, True, {"key": "val"}])

        assert len(result) == 4
        assert result[0] == b"hello"
        assert result[1] == b"42"
        assert result[2] == b"true"
        parsed = json.loads(result[3].decode("utf-8"))
        assert parsed == {"key": "val"}


class TestParsePropertyCountResponse:
    """Test parse_property_count_response function."""

    def test_valid_count(self) -> None:
        """Test parsing valid property count."""
        result = parse_property_count_response({"count": 5})

        assert result == 5

    def test_zero_count(self) -> None:
        """Test parsing zero count."""
        result = parse_property_count_response({"count": 0})

        assert result == 0

    def test_none_response(self) -> None:
        """Test parsing None response."""
        result = parse_property_count_response(None)

        assert result == 0

    def test_empty_dict(self) -> None:
        """Test parsing empty dict."""
        result = parse_property_count_response({})

        assert result == 0

    def test_missing_count_key(self) -> None:
        """Test response without count key."""
        result = parse_property_count_response({"other_key": 10})

        assert result == 0

    def test_negative_count(self) -> None:
        """Test negative count returns 0."""
        result = parse_property_count_response({"count": -5})

        assert result == 0

    def test_non_int_count(self) -> None:
        """Test non-integer count returns 0."""
        result = parse_property_count_response({"count": "5"})

        assert result == 0

    def test_float_count(self) -> None:
        """Test float count returns 0."""
        result = parse_property_count_response({"count": 5.5})

        assert result == 0

    def test_non_dict_response(self) -> None:
        """Test non-dict response returns 0."""
        result = parse_property_count_response([1, 2, 3])

        assert result == 0

    def test_string_response(self) -> None:
        """Test string response returns 0."""
        result = parse_property_count_response("not a dict")

        assert result == 0


class TestParsePropertyValuesResponse:
    """Test parse_property_values_response function."""

    def test_valid_properties(self) -> None:
        """Test parsing valid properties response."""
        response = {
            "properties": [
                {"name": "config", "value": b'{"node_id": "test"}'},
                {"name": "params", "value": b"{}"},
            ]
        }

        result = parse_property_values_response(response)

        assert len(result) == 2
        assert result[0]["name"] == "config"
        assert result[1]["name"] == "params"
        # Verify byte values are preserved unchanged
        assert result[0]["value"] == b'{"node_id": "test"}'
        assert result[1]["value"] == b"{}"

    def test_empty_properties(self) -> None:
        """Test parsing empty properties list."""
        response: dict[str, list[Any]] = {"properties": []}

        result = parse_property_values_response(response)

        assert result == []

    def test_none_response(self) -> None:
        """Test parsing None response."""
        result = parse_property_values_response(None)

        assert result == []

    def test_missing_properties_key(self) -> None:
        """Test response without properties key."""
        response = {"other_key": "value"}

        result = parse_property_values_response(response)

        assert result == []

    def test_non_dict_response(self) -> None:
        """Test non-dict response."""
        result = parse_property_values_response("not a dict")

        assert result == []

    def test_non_list_properties(self) -> None:
        """Test properties that isn't a list."""
        response = {"properties": "not a list"}

        result = parse_property_values_response(response)

        assert result == []

    def test_complex_property_values(self) -> None:
        """Test properties with complex nested values."""
        response = {
            "properties": [
                {
                    "name": "config",
                    "type": 1,
                    "flags": 0,
                    "value": b'{"devices":[{"name":"Light","params":[]}]}',
                },
            ]
        }

        result = parse_property_values_response(response)

        assert len(result) == 1
        assert result[0]["name"] == "config"
        assert result[0]["type"] == 1
        assert result[0]["flags"] == 0
        # Parse and validate JSON structure
        parsed = json.loads(result[0]["value"].decode("utf-8"))
        assert "devices" in parsed
        assert isinstance(parsed["devices"], list)
        assert len(parsed["devices"]) == 1
        assert parsed["devices"][0]["name"] == "Light"
        assert isinstance(parsed["devices"][0]["params"], list)

    def test_properties_with_non_dict_items(self) -> None:
        """Test properties list with non-dict items filters them out."""
        response = {
            "properties": [
                {"name": "valid", "value": b"test"},
                "invalid_string_item",
                123,
                None,
                {"name": "another_valid", "value": b"data"},
            ]
        }

        result = parse_property_values_response(response)

        # Should only include dict items
        assert len(result) == 2
        assert result[0]["name"] == "valid"
        assert result[1]["name"] == "another_valid"


class TestConvertValuesToEspFormatErrors:
    """Test error handling in convert_values_to_esp_format."""

    def test_bytes_value_passthrough(self) -> None:
        """Test that bytes values are passed through unchanged."""
        raw_bytes = b"\x00\x01\x02\xff"
        result = convert_values_to_esp_format([raw_bytes])

        assert result == [raw_bytes]

    def test_non_serializable_value_raises(self) -> None:
        """Test that non-serializable values raise an error."""

        class NonSerializable:
            """A class that cannot be JSON serialized."""

        with pytest.raises((TypeError, ValueError)):
            convert_values_to_esp_format([NonSerializable()])
