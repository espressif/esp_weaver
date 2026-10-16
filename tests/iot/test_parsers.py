# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver parsers module."""

import json

import pytest

from custom_components.esp_weaver.iot.parsers.property_parser import (
    decode_property_value,
    firmware_version_from_config,
    firmware_version_from_properties,
    get_raw_params,
    model_id_from_config,
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


class TestGetRawParams:
    """The params property, as the device sent it."""

    @pytest.mark.parametrize("properties", [None, []])
    def test_nothing_reported_is_none(self, properties: list | None) -> None:
        """Test no properties yields no params."""
        assert get_raw_params(properties) is None

    @pytest.mark.parametrize(
        "value",
        [
            json.dumps({"Light": {"power": True}}),
            b'{"Light": {"power": true}}',
            {"Light": {"power": True}},
        ],
        ids=["str", "bytes", "dict"],
    )
    def test_every_shape_of_value_is_read(self, value: object) -> None:
        """Test JSON text, JSON bytes and an already decoded dict all count."""
        assert get_raw_params([{"name": "params", "value": value}]) == {
            "Light": {"power": True}
        }

    def test_the_nested_structure_is_preserved(self) -> None:
        """Test what comes back is the device's own shape, untouched."""
        nested = {"level1": {"level2": {"level3": {"value": 123}}}}
        assert get_raw_params([{"name": "params", "value": json.dumps(nested)}]) == (
            nested
        )

    @pytest.mark.parametrize(
        "properties",
        [
            [{"name": "params", "value": "not valid json {"}],
            [{"name": "params", "value": ""}],
            [{"name": "params"}],
            [{"name": "other", "value": '{"Light": {"power": true}}'}],
        ],
        ids=["invalid_json", "empty", "no_value", "other_name"],
    )
    def test_an_unusable_property_is_none(self, properties: list) -> None:
        """Test a property that is not readable params yields none."""
        assert get_raw_params(properties) is None

    def test_an_unnamed_property_counts_as_params(self) -> None:
        """Test firmware that reports params without naming the property."""
        assert get_raw_params([{"value": '{"Light": {"power": true}}'}]) == {
            "Light": {"power": True}
        }

    def test_config_property_is_not_a_parameter(self) -> None:
        """Test the node's own config is filtered out rather than read as params."""
        properties = [
            {"name": "config", "value": '{"node_id": "test"}'},
            {"name": "params", "value": '{"Light": {"power": true}}'},
        ]
        assert get_raw_params(properties) == {"Light": {"power": True}}

    def test_decode_bytes_non_dict_json(self) -> None:
        """Test decoding bytes that parse to non-dict (e.g., array)."""
        assert decode_property_value(b"[1, 2, 3]") is None

    @pytest.mark.parametrize("value", ["[1, 2, 3]", "123", '"hello"'])
    def test_decode_string_non_dict_json(self, value: str) -> None:
        """Test decoding a string that parses to something other than a dict."""
        assert decode_property_value(value) is None


class TestFirmwareVersion:
    """The version a node's config names, which an OTA is what changes."""

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            ({"info": {"fw_version": "1.0.2"}}, "1.0.2"),
            ({"info": {"fw_version": 2}}, "2"),
            ({"info": {"fw_version": ""}}, None),
            ({"info": {}}, None),
            ({"info": "not a mapping"}, None),
            ({}, None),
            (None, None),
        ],
        ids=[
            "version",
            "not_a_string",
            "empty",
            "no_version",
            "info_not_a_mapping",
            "no_info",
            "no_config",
        ],
    )
    def test_version_is_read_from_a_config(
        self, config: dict[str, object] | None, expected: str | None
    ) -> None:
        """Test a version is taken where one is named and nothing invented."""
        assert firmware_version_from_config(config) == expected

    @pytest.mark.parametrize(
        "value",
        [
            {"info": {"fw_version": "1.0.2"}},
            json.dumps({"info": {"fw_version": "1.0.2"}}),
        ],
        ids=["dict", "json"],
    )
    def test_version_is_read_from_a_local_read(self, value: object) -> None:
        """Test the config property is found and decoded on the way."""
        properties = [{"name": "config", "value": value}]

        assert firmware_version_from_properties(properties) == "1.0.2"

    def test_a_read_without_a_config_names_no_version(self) -> None:
        """Test params alone leave the version as it was."""
        properties = [{"name": "params", "value": json.dumps({"Light": {"on": True}})}]

        assert firmware_version_from_properties(properties) is None


class TestModelId:
    """The model the firmware set on purpose, which is the device's own word."""

    @pytest.mark.parametrize(
        ("config", "identity_advertised", "expected"),
        [
            ({"info": {"model": "A1", "project_name": "lamp_fw"}}, True, "A1"),
            (
                {"info": {"model": "led_light", "project_name": "led_light"}},
                True,
                "led_light",
            ),
            ({"info": {"model": 7}}, True, "7"),
            (
                {"info": {"model": "led_light", "project_name": "led_light"}},
                False,
                None,
            ),
            ({"info": {"model": "*", "project_name": "led_light"}}, True, None),
            ({"info": {"model": "", "project_name": "led_light"}}, True, None),
            ({"info": {"project_name": "led_light"}}, True, None),
            ({"info": "not a mapping"}, True, None),
            ({}, True, None),
            (None, True, None),
        ],
        ids=[
            "set_on_purpose",
            "same_as_project_name_but_advertised",
            "not_a_string",
            "not_advertised",
            "generic",
            "empty",
            "no_model",
            "info_not_a_mapping",
            "no_info",
            "no_config",
        ],
    )
    def test_only_a_model_set_on_purpose_counts(
        self,
        config: dict[str, object] | None,
        identity_advertised: bool,
        expected: str | None,
    ) -> None:
        """Test mDNS says whether firmware set a model; ``*`` identifies no hardware."""
        assert (
            model_id_from_config(config, identity_advertised=identity_advertised)
            == expected
        )
