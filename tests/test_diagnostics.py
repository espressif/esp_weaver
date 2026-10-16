# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver diagnostics."""

from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.const import CONF_CUSTOM_POP
from custom_components.esp_weaver.diagnostics import (
    _redact_ip_address,
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
    redact,
)
from custom_components.esp_weaver.profiles.loader import CUSTOM_PROFILE_DIR

from .conftest import (
    NODE_PRODUCT,
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    create_mock_config_entry,
)


@pytest.fixture(autouse=True)
def _entries_can_set_up(node_product_in_store: None) -> None:
    """Every entry here is bound to the test node's product; keep its content stored."""


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

        result = redact(data)

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

        result = redact(data)

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

        result = redact(data)

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

        result = redact(data)

        assert result["items"][0]["pop"] == "**REDACTED**"
        assert result["items"][1]["pop"] == "**REDACTED**"

    def test_redact_list_with_non_dict_items(self) -> None:
        """Test list containing non-dictionary items."""
        data = {"items": ["string", 123, None, {"pop": "secret"}]}

        result = redact(data)

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

        result = redact(data)

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

        result = redact(data)

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
        mock_coordinator.is_available = True

        mock_esp_api.devices = {TEST_NODE_ID: {"ip": TEST_HOST}}
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.get_device_data = MagicMock(return_value={"test": "data"})

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert "entry" in result
        assert "coordinator" in result
        assert "api_status" in result
        assert result["entry"]["title"] == TEST_DEVICE_NAME

        # Verify API status structure
        assert "device_registered" in result["api_status"], (
            "Expected 'device_registered' key in API status"
        )
        assert "device_available" in result["api_status"], (
            "Expected 'device_available' key in API status"
        )
        assert "discovery_completed" in result["api_status"], (
            "Expected 'discovery_completed' key in API status"
        )
        assert result["api_status"]["device_registered"] is True
        assert result["api_status"]["device_available"] is True
        assert result["api_status"]["discovery_completed"] is True

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
        mock_coordinator.is_available = True

        mock_esp_api.devices = {TEST_NODE_ID: {}}
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.is_discovery_completed = MagicMock(return_value=True)
        mock_esp_api.get_device_data = MagicMock(return_value={})

        result = await async_get_config_entry_diagnostics(hass, entry)

        # PoP should be redacted - unconditional assertion
        entry_data = result["entry"]["data"]
        assert CONF_CUSTOM_POP in entry_data, (
            f"Expected {CONF_CUSTOM_POP} key in entry data"
        )
        assert entry_data[CONF_CUSTOM_POP] == "**REDACTED**", (
            "Expected PoP to be redacted"
        )


class TestProfileFilesSection:
    """Diagnostics say which profile files were read and which were rejected."""

    @pytest.fixture(autouse=True)
    def _config_dir(self, own_config_dir: Path) -> None:
        """Write profiles somewhere this test owns, not the shared config."""

    @pytest.fixture
    def entry(
        self,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
    ) -> MagicMock:
        """A loaded entry, since diagnostics are only offered for those."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api
        return entry

    async def test_it_names_the_custom_directory_without_the_household_path(
        self,
        hass: HomeAssistant,
        entry: MagicMock,
    ) -> None:
        """Support asking "where do I put my file" gets the folder, not the disk.

        Where the configuration directory lives is the household's business;
        the folder under it is all a bug report needs.
        """

        result = await async_get_config_entry_diagnostics(hass, entry)

        section = result["profile_files"]
        assert section["custom_dir"] == CUSTOM_PROFILE_DIR
        assert "builtin_dir" not in section

    async def test_it_lists_the_products_on_offer_and_the_binding(
        self,
        hass: HomeAssistant,
        entry: MagicMock,
    ) -> None:
        """Which product a device is bound to is the first thing to check."""
        entry.options = {
            "profile": {"id": NODE_PRODUCT, "source": "builtin", "hash": "abc"}
        }

        result = await async_get_config_entry_diagnostics(hass, entry)

        section = result["profile_files"]
        assert NODE_PRODUCT in [p["id"] for p in section["products"]]
        assert section["binding"] == {
            "id": NODE_PRODUCT,
            "source": "builtin",
            "hash": "abc",
        }

    async def test_an_entry_without_a_binding_says_so(
        self,
        hass: HomeAssistant,
        entry: MagicMock,
    ) -> None:
        """An entry without a binding has none to report."""
        entry.options = {}

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["profile_files"]["binding"] is None

    async def test_it_carries_the_rejected_files(
        self,
        hass: HomeAssistant,
        entry: MagicMock,
    ) -> None:
        """A download is often all we get from a user who cannot share logs."""
        custom_dir = Path(hass.config.path(CUSTOM_PROFILE_DIR))
        await hass.async_add_executor_job(partial(custom_dir.mkdir, exist_ok=True))
        await hass.async_add_executor_job(
            (custom_dir / "mine.yaml").write_text,
            "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n",
        )

        result = await async_get_config_entry_diagnostics(hass, entry)

        problems = result["profile_files"]["problems"]
        assert len(problems) == 1
        assert problems[0]["kind"] == "invalid"
        assert problems[0]["file"].endswith("esp_weaver/mine.yaml")
        assert "extra keys not allowed" in problems[0]["detail"]


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
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.get_device_data = MagicMock(
            return_value={"ip": TEST_HOST, "test": "data"}
        )

        result = await async_get_device_diagnostics(hass, entry, mock_device_entry)

        assert "device" in result
        assert "entry" in result
        assert result["device"]["name"] == TEST_DEVICE_NAME

        # Verify device data is returned
        assert "device_data" in result, "Expected 'device_data' key in result"
        assert "ip" in result["device_data"], "Expected 'ip' key in device_data"
        # IP should be redacted
        assert "***" in result["device_data"]["ip"], (
            "Expected IP to be redacted with ***"
        )

    async def test_device_diagnostics_includes_profile_preview(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
        mock_device_entry: MagicMock,
    ) -> None:
        """Test device diagnostics rehearses the profile path."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api

        mock_esp_api.devices = {TEST_NODE_ID: {}}
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.get_device_data = MagicMock(return_value={})
        mock_esp_api.get_reported_config = MagicMock(
            return_value=(
                {
                    "node_id": TEST_NODE_ID,
                    "devices": [
                        {
                            "name": "Sensor",
                            "type": "esp.device.sensor",
                            "params": [{"name": "Ambient Temperature"}],
                        }
                    ],
                },
                {},
            )
        )

        result = await async_get_device_diagnostics(hass, entry, mock_device_entry)

        preview = result["profiles"]
        assert preview["reported"] == [
            {
                "kind": "device",
                "name": "Sensor",
                "type": "esp.device.sensor",
                "params": ["Ambient Temperature"],
            }
        ]
        # The profiles ship on disk, so loading them must have produced sources.
        assert preview["registry"]["sources"]
        assert set(preview["comparison"]) == {
            "matched",
            "only_in_bindings",
            "only_in_registry",
        }

    async def test_device_diagnostics_without_a_reported_config(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
        mock_esp_api: MagicMock,
        mock_device_entry: MagicMock,
    ) -> None:
        """Test the preview says so when discovery has not run yet."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.api = mock_esp_api

        mock_esp_api.devices = {TEST_NODE_ID: {}}
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.get_device_data = MagicMock(return_value={})
        mock_esp_api.get_reported_config = MagicMock(return_value=({}, {}))

        result = await async_get_device_diagnostics(hass, entry, mock_device_entry)

        assert "profiles" not in result
        assert "not reported a config" in result["profiles_error"]

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
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        mock_esp_api.get_device_data = MagicMock(return_value={})

        result = await async_get_device_diagnostics(hass, entry, mock_device_entry)

        assert "connectivity" in result
        assert result["connectivity"]["available"] is True
        assert result["connectivity"]["coordinator_available"] is True
