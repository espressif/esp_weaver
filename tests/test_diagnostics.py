# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver diagnostics."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.const import (
    DIAG_API_STATUS,
    DIAG_COORDINATOR,
    DIAG_DEVICE,
    DIAG_ENTRY,
)
from custom_components.esp_weaver.diagnostics import (
    _process_dict_for_redaction,
    _redact_ip_address,
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.esp_weaver.iot.specs.keys import CONF_CUSTOM_POP

from .conftest import (
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    create_mock_config_entry,
)


class TestIPRedaction:
    """Test IP address redaction."""

    def test_redact_ipv4_address(self) -> None:
        """Test IPv4 address redaction."""
        result = _redact_ip_address("192.168.1.100")
        assert result == "***.***.***.100"
        assert "192" not in result
        assert "168" not in result
        assert "100" in result

    def test_redact_empty_address(self) -> None:
        """Test empty address redaction."""
        result = _redact_ip_address("")
        assert result == "**REDACTED**"

    def test_redact_invalid_format(self) -> None:
        """Test invalid format address redaction."""
        result = _redact_ip_address("not-an-ip")
        assert result == "**REDACTED**"

    def test_redact_none_input(self) -> None:
        """Test None input handling."""
        result = _redact_ip_address(None)  # type: ignore[arg-type]
        assert result == "**REDACTED**"

    def test_redact_localhost(self) -> None:
        """Test localhost address redaction."""
        result = _redact_ip_address("127.0.0.1")
        assert result == "***.***.***.1"
        assert "127" not in result

    def test_redact_partial_octets(self) -> None:
        """Test malformed address with partial octets."""
        result = _redact_ip_address("192.168.1")
        assert result == "**REDACTED**"


class TestDictRedaction:
    """Test dictionary redaction."""

    def test_redact_sensitive_keys(self) -> None:
        """Test sensitive keys are fully redacted."""
        data = {
            "normal_key": "value",
            "pop": "secret_pop",
            "password": "secret_password",
            "token": "secret_token",
        }

        result = _process_dict_for_redaction(
            data,
            to_redact={"pop", "password", "token"},
            to_partially_redact=set(),
        )

        assert result["normal_key"] == "value"
        assert result["pop"] == "**REDACTED**"
        assert result["password"] == "**REDACTED**"
        assert result["token"] == "**REDACTED**"

    def test_redact_ip_keys(self) -> None:
        """Test IP address keys are partially redacted."""
        data = {
            "host": "192.168.1.100",
            "ip": "10.0.0.1",
        }

        result = _process_dict_for_redaction(
            data,
            to_redact=set(),
            to_partially_redact={"host", "ip"},
        )

        assert "***" in result["host"]
        assert "***" in result["ip"]

    def test_redact_nested_dict(self) -> None:
        """Test nested dictionary redaction."""
        data = {
            "outer": {
                "pop": "secret",
                "host": "192.168.1.1",
                "normal": "value",
            }
        }

        result = _process_dict_for_redaction(
            data,
            to_redact={"pop"},
            to_partially_redact={"host"},
        )

        assert result["outer"]["pop"] == "**REDACTED**"
        assert "***" in result["outer"]["host"]
        assert result["outer"]["normal"] == "value"

    def test_redact_list_of_dicts(self) -> None:
        """Test list of dictionaries redaction."""
        data = {
            "items": [
                {"pop": "secret1"},
                {"pop": "secret2"},
            ]
        }

        result = _process_dict_for_redaction(
            data,
            to_redact={"pop"},
            to_partially_redact=set(),
        )

        assert result["items"][0]["pop"] == "**REDACTED**"
        assert result["items"][1]["pop"] == "**REDACTED**"

    def test_redact_list_with_non_dict_items(self) -> None:
        """Test list containing non-dictionary items."""
        data = {"items": ["string", 123, None, {"pop": "secret"}]}

        result = _process_dict_for_redaction(
            data,
            to_redact={"pop"},
            to_partially_redact=set(),
        )

        # Non-dict items should be preserved
        assert result["items"][0] == "string"
        assert result["items"][1] == 123
        assert result["items"][2] is None
        # Dict items should be processed
        assert result["items"][3]["pop"] == "**REDACTED**"

    def test_redact_none_values_in_dict(self) -> None:
        """Test None values in dictionaries."""
        data = {
            "pop": None,
            "host": None,
            "normal": None,
        }

        result = _process_dict_for_redaction(
            data,
            to_redact={"pop"},
            to_partially_redact={"host"},
        )

        # None values with sensitive keys should still be redacted
        assert result["pop"] == "**REDACTED**"
        # Partial redaction of None should produce redacted output
        assert result["host"] == "**REDACTED**"
        assert result["normal"] is None

    def test_redact_mixed_data_types(self) -> None:
        """Test mixed data types (integers, booleans, floats)."""
        data = {
            "count": 42,
            "enabled": True,
            "ratio": 3.14,
            "pop": "secret",
        }

        result = _process_dict_for_redaction(
            data,
            to_redact={"pop"},
            to_partially_redact=set(),
        )

        assert result["count"] == 42
        assert result["enabled"] is True
        assert result["ratio"] == 3.14
        assert result["pop"] == "**REDACTED**"


class TestConfigEntryDiagnostics:
    """Test config entry diagnostics."""

    async def test_get_config_entry_diagnostics(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test config entry diagnostics generation."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api
        mock_coordinator.discovery_completed = True
        mock_coordinator.is_available = True

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST}}
        mock_esp_api.is_device_available = MagicMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.get_device_data = AsyncMock(return_value={"test": "data"})

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert DIAG_ENTRY in result
        assert DIAG_COORDINATOR in result
        assert DIAG_API_STATUS in result
        assert result[DIAG_ENTRY]["title"] == TEST_DEVICE_NAME

        # Verify API status structure
        assert "device_registered" in result[DIAG_API_STATUS], (
            "Expected 'device_registered' key in API status"
        )
        assert "device_available" in result[DIAG_API_STATUS], (
            "Expected 'device_available' key in API status"
        )
        assert "discovery_completed" in result[DIAG_API_STATUS], (
            "Expected 'discovery_completed' key in API status"
        )
        assert result[DIAG_API_STATUS]["device_registered"] is True
        assert result[DIAG_API_STATUS]["device_available"] is True
        assert result[DIAG_API_STATUS]["discovery_completed"] is True

    async def test_diagnostics_with_pop_redacted(
        self,
        hass: HomeAssistant,
        mock_config_entry_data_with_pop: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test PoP is redacted in diagnostics."""
        entry = create_mock_config_entry(mock_config_entry_data_with_pop)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api
        mock_coordinator.discovery_completed = True
        mock_coordinator.is_available = True

        mock_esp_api.devices = {TEST_NODE_ID: {}}
        mock_esp_api.is_device_available = MagicMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.get_device_data = AsyncMock(return_value={})

        result = await async_get_config_entry_diagnostics(hass, entry)

        # PoP should be redacted - unconditional assertion
        entry_data = result[DIAG_ENTRY]["data"]
        assert CONF_CUSTOM_POP in entry_data, (
            f"Expected {CONF_CUSTOM_POP} key in entry data"
        )
        assert entry_data[CONF_CUSTOM_POP] == "**REDACTED**", (
            "Expected PoP to be redacted"
        )

    async def test_diagnostics_without_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test diagnostics when coordinator is not available."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert DIAG_ENTRY in result
        assert DIAG_COORDINATOR in result
        assert result[DIAG_COORDINATOR]["available"] is False

    async def test_diagnostics_device_data_error(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test diagnostics handles device data fetch error."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api

        mock_esp_api.devices = {TEST_NODE_ID: {}}
        mock_esp_api.is_device_available = MagicMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.get_device_data = AsyncMock(
            side_effect=RuntimeError("Connection error")
        )

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert "device_data_error" in result
        # Verify error message is captured (generic message, not exception details)
        assert isinstance(result["device_data_error"], str)
        assert "Failed to retrieve device data" in result["device_data_error"]


class TestDeviceDiagnostics:
    """Test device diagnostics."""

    async def test_get_device_diagnostics(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
        mock_device_entry: MagicMock,
    ) -> None:
        """Test device diagnostics generation."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST}}
        mock_esp_api.is_device_available = MagicMock(return_value=True)
        mock_esp_api.get_device_data = AsyncMock(
            return_value={"ip": TEST_HOST, "test": "data"}
        )

        result = await async_get_device_diagnostics(hass, entry, mock_device_entry)

        assert DIAG_DEVICE in result
        assert DIAG_ENTRY in result
        assert result[DIAG_DEVICE]["name"] == TEST_DEVICE_NAME

        # Verify device data is returned
        assert "device_data" in result, "Expected 'device_data' key in result"
        assert "ip" in result["device_data"], "Expected 'ip' key in device_data"
        # IP should be redacted
        assert "***" in result["device_data"]["ip"], (
            "Expected IP to be redacted with ***"
        )

    async def test_device_diagnostics_connectivity_info(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
        mock_device_entry: MagicMock,
    ) -> None:
        """Test device diagnostics includes connectivity info."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api
        mock_coordinator.is_available = True

        mock_esp_api.devices = {TEST_NODE_ID: {}}
        mock_esp_api.is_device_available = MagicMock(return_value=True)
        mock_esp_api.get_device_data = AsyncMock(return_value={})

        result = await async_get_device_diagnostics(hass, entry, mock_device_entry)

        assert "connectivity" in result
        assert result["connectivity"]["available"] is True
        assert result["connectivity"]["coordinator_available"] is True
