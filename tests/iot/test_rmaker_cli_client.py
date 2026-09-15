# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP RainMaker CLI Client."""

from collections.abc import Iterator
import json
import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.esp_weaver.iot.cloud.rmaker_cli_client as rmaker_module
from custom_components.esp_weaver.iot.cloud.rmaker_cli_client import (
    CloudDeviceInfo,
    RainMakerCLIClient,
    _check_cli_installed,
    _ensure_cli_available,
    _get_proto_fix_env,
    _install_cli,
)

# =============================================================================
# Dataclass Tests
# =============================================================================


class TestCloudDeviceInfo:
    """Test CloudDeviceInfo dataclass."""

    def test_create_with_required_fields(self) -> None:
        """Test creating CloudDeviceInfo with required fields only."""
        device = CloudDeviceInfo(
            node_id="ABC123456789",
            name="Test Device",
        )
        assert device.node_id == "ABC123456789"
        assert device.name == "Test Device"
        assert device.connected is False

    def test_create_with_all_fields(self) -> None:
        """Test creating CloudDeviceInfo with all fields."""
        device = CloudDeviceInfo(
            node_id="ABC123456789",
            name="Test Device",
            connected=True,
        )
        assert device.connected is True


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestCheckCliInstalled:
    """Test _check_cli_installed function."""

    def test_cli_installed(self) -> None:
        """Test when CLI is installed."""
        with patch("shutil.which", return_value="/usr/bin/esp-rainmaker-cli"):
            assert _check_cli_installed() is True

    def test_cli_not_installed(self) -> None:
        """Test when CLI is not installed."""
        with patch("shutil.which", return_value=None):
            assert _check_cli_installed() is False


class TestGetProtoFixEnv:
    """Test _get_proto_fix_env function."""

    def setup_method(self) -> None:
        """Reset proto fix env cache before each test."""
        rmaker_module._proto_fix_env = None

    def test_rmaker_tools_installed(self) -> None:
        """Test when rmaker_tools is installed."""
        mock_rmaker_tools = MagicMock()
        mock_rmaker_tools.__file__ = "/path/to/rmaker_tools/__init__.py"

        with (
            patch.dict("sys.modules", {"rmaker_tools": mock_rmaker_tools}),
            patch("os.environ.copy", return_value={}),
        ):
            env = _get_proto_fix_env()
            assert "PYTHONPATH" in env
            assert "rmaker_prov" in env["PYTHONPATH"]

    def test_rmaker_tools_not_installed(self) -> None:
        """Test when rmaker_tools is not installed."""
        with (
            patch.dict("sys.modules", {"rmaker_tools": None}),
            patch("os.environ.copy", return_value={"EXISTING": "value"}),
        ):
            # AttributeError on .__file__ access should be caught
            env = _get_proto_fix_env()
            assert "EXISTING" in env

    def test_existing_pythonpath_preserved(self) -> None:
        """Test that existing PYTHONPATH is preserved."""
        mock_rmaker_tools = MagicMock()
        mock_rmaker_tools.__file__ = "/path/to/rmaker_tools/__init__.py"

        with (
            patch.dict("sys.modules", {"rmaker_tools": mock_rmaker_tools}),
            patch("os.environ.copy", return_value={"PYTHONPATH": "/existing/path"}),
        ):
            env = _get_proto_fix_env()
            assert "/existing/path" in env["PYTHONPATH"]
            assert "rmaker_prov" in env["PYTHONPATH"]


class TestInstallCli:
    """Test _install_cli function."""

    def setup_method(self) -> None:
        """Reset install flag before each test."""
        rmaker_module._install_attempted = False

    @pytest.fixture(autouse=True)
    def writable(self) -> Iterator[None]:
        """Say the environment can be installed into, whatever the test machine is."""
        with patch(
            "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._environment_is_writable",
            return_value=True,
        ):
            yield

    def test_a_read_only_environment_is_not_attempted(self) -> None:
        """Home Assistant is very often a read-only container.

        pip would spend its whole timeout failing, and the message it produces
        is about permissions rather than about what to do next.
        """
        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._environment_is_writable",
                return_value=False,
            ),
            patch("subprocess.run") as mock_run,
        ):
            assert _install_cli() is False

        mock_run.assert_not_called()

    def test_install_success(self) -> None:
        """Test successful CLI installation."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            assert _install_cli() is True

    def test_install_failure(self) -> None:
        """Test failed CLI installation."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "pip error"

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
                return_value=False,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            assert _install_cli() is False

    def test_install_only_attempted_once(self) -> None:
        """Test that installation is only attempted once per session."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
                return_value=False,
            ),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            # First call attempts installation
            _install_cli()
            assert mock_run.call_count == 1

            # Second call should not attempt again
            _install_cli()
            assert mock_run.call_count == 1  # Still 1, not 2

    def test_install_timeout(self) -> None:
        """Test installation timeout handling."""
        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
                return_value=False,
            ),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)),
        ):
            assert _install_cli() is False


class TestEnsureCliAvailable:
    """Test _ensure_cli_available function."""

    def setup_method(self) -> None:
        """Reset install flag before each test."""
        rmaker_module._install_attempted = False

    @pytest.fixture(autouse=True)
    def writable(self) -> Iterator[None]:
        """Say the environment can be installed into. See TestInstallCli."""
        with patch(
            "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._environment_is_writable",
            return_value=True,
        ):
            yield

    def test_cli_already_installed(self) -> None:
        """Test when CLI is already installed."""
        with patch(
            "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
            return_value=True,
        ):
            assert _ensure_cli_available() is True

    def test_cli_not_installed_install_succeeds(self) -> None:
        """Test when CLI is not installed and installation succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
                side_effect=[False, True],  # Not installed, then installed
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            assert _ensure_cli_available() is True

    def test_cli_not_installed_install_fails(self) -> None:
        """Test when CLI is not installed and installation fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._check_cli_installed",
                return_value=False,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            assert _ensure_cli_available() is False


# =============================================================================
# RainMakerCLIClient Initialization Tests
# =============================================================================


class TestRainMakerCLIClientInit:
    """Test RainMakerCLIClient initialization."""

    def test_init_global_region(self) -> None:
        """Test initialization with global region."""
        client = RainMakerCLIClient(region="global")
        assert client._region == "global"
        assert client._profile == "global"

    def test_init_china_region(self) -> None:
        """Test initialization with china region."""
        client = RainMakerCLIClient(region="china")
        assert client._region == "china"
        assert client._profile == "china"

    def test_init_default_region(self) -> None:
        """Test initialization with default region."""
        client = RainMakerCLIClient()
        assert client._region == "global"
        assert client._profile == "global"


# =============================================================================
# CLI Command Execution Tests
# =============================================================================


class TestRunCli:
    """Test _run_cli method."""

    def test_cli_not_available(self) -> None:
        """Test when CLI is not available and auto-install fails."""
        client = RainMakerCLIClient()
        with patch(
            "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._ensure_cli_available",
            return_value=False,
        ):
            result = client._run_cli(["test"])
            assert result["success"] is False
            # The command to run, not just the fact that it did not.
            assert "pip install esp-rainmaker-cli --no-deps" in result["error"]

    def test_cli_success(self) -> None:
        """Test successful CLI command."""
        client = RainMakerCLIClient()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success output"
        mock_result.stderr = ""

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._ensure_cli_available",
                return_value=True,
            ),
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._get_proto_fix_env",
                return_value={},
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = client._run_cli(["test"])
            assert result["success"] is True
            assert result["output"] == "success output"

    def test_cli_failure(self) -> None:
        """Test failed CLI command."""
        client = RainMakerCLIClient()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error message"

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._ensure_cli_available",
                return_value=True,
            ),
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._get_proto_fix_env",
                return_value={},
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = client._run_cli(["test"])
            assert result["success"] is False
            assert result["error"] == "error message"

    def test_cli_timeout(self) -> None:
        """Test CLI command timeout."""
        client = RainMakerCLIClient()

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._ensure_cli_available",
                return_value=True,
            ),
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._get_proto_fix_env",
                return_value={},
            ),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)),
        ):
            result = client._run_cli(["test"], timeout=30)
            assert result["success"] is False
            assert "timed out" in result["error"]

    def test_cli_exception(self) -> None:
        """Test CLI command with exception."""
        client = RainMakerCLIClient()

        with (
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._ensure_cli_available",
                return_value=True,
            ),
            patch(
                "custom_components.esp_weaver.iot.cloud.rmaker_cli_client._get_proto_fix_env",
                return_value={},
            ),
            patch("subprocess.run", side_effect=OSError("Process error")),
        ):
            result = client._run_cli(["test"])
            assert result["success"] is False
            assert "Process error" in result["error"]


class TestRunCliAsync:
    """Test _run_cli_async method."""

    async def test_async_wrapper(self) -> None:
        """Test async wrapper calls _run_cli."""
        client = RainMakerCLIClient()

        with patch.object(
            client, "_run_cli", return_value={"success": True, "output": "test"}
        ) as mock_run:
            result = await client._run_cli_async(["test"], timeout=10)
            assert result["success"] is True
            mock_run.assert_called_once_with(["test"], 10, None)


# =============================================================================
# Authentication Tests
# =============================================================================


class TestLogin:
    """Test login method."""

    async def test_login_success(self) -> None:
        """Test successful login passes the password in the environment."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={"success": True, "output": "Login successful"},
            ) as mock_run,
            patch.object(
                client,
                "check_login_status",
                new_callable=AsyncMock,
                return_value={"logged_in": True},
            ),
        ):
            result = await client.login("test@example.com", "password123")
            assert result["success"] is True
            assert result["email"] == "test@example.com"
            assert result["region"] == "global"
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args == ["login", "--email", "test@example.com"]
            assert "password123" not in call_args
            assert mock_run.call_args.kwargs["extra_env"] == {
                "ESP_RAINMAKER_PASSWORD": "password123"
            }

    async def test_login_cli_success_but_verification_fails(self) -> None:
        """Test login CLI exits 0 but actual verification shows not logged in."""
        client = RainMakerCLIClient(region="china")

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={"success": True, "output": ""},
            ),
            patch.object(
                client,
                "check_login_status",
                new_callable=AsyncMock,
                return_value={"logged_in": False, "error": "Not logged in"},
            ),
        ):
            result = await client.login("test@example.com", "password123")
            assert result["success"] is False
            assert (
                "region" in result["error"].lower()
                or "server" in result["error"].lower()
            )

    async def test_login_failure(self) -> None:
        """Test failed login."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={"success": False, "error": "Invalid credentials"},
            ),
        ):
            result = await client.login("test@example.com", "wrong_password")
            assert result["success"] is False
            assert "Invalid credentials" in result["error"]


class TestCheckLoginStatus:
    """Test check_login_status method."""

    async def test_logged_in(self) -> None:
        """Test when user is logged in."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "user info"},
        ):
            result = await client.check_login_status()
            assert result["logged_in"] is True

    async def test_not_logged_in_explicit(self) -> None:
        """Test when not logged in with explicit indicator."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "Not logged in"},
        ):
            result = await client.check_login_status()
            assert result["logged_in"] is False

    async def test_not_logged_in_despite_success_returncode(self) -> None:
        """Test CLI exits 0 but output says 'not logged in' (getuserinfo bug)."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "output": (
                    "Using profile: global\n"
                    "\n❌ Not logged in to profile 'global'\n"
                    "Please login to this builtin profile first:\n"
                    "   esp-rainmaker-cli login"
                ),
            },
        ):
            result = await client.check_login_status()
            assert result["logged_in"] is False

    async def test_not_logged_in_unauthorized(self) -> None:
        """Test when not logged in with unauthorized error."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "unauthorized access"},
        ):
            result = await client.check_login_status()
            assert result["logged_in"] is False

    async def test_unknown_error(self) -> None:
        """Test when unknown error occurs."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "Network error"},
        ):
            result = await client.check_login_status()
            assert result["logged_in"] is False
            assert "Network error" in result["error"]

    @pytest.mark.parametrize(
        ("output", "email"),
        [
            (
                "User Name (Email): Someone@Example.com\nUser Id: 1",
                "someone@example.com",
            ),
            ("\x1b[1mUser Name (email):\x1b[0m a@b.c", "a@b.c"),
            ("Using profile: global\nuser name: a@b.c\n", "a@b.c"),
            ("User Id: 1\nSuper Admin: False", None),
        ],
        ids=["titled", "coloured", "plain", "unnamed"],
    )
    async def test_the_signed_in_account_is_read_from_the_output(
        self, output: str, email: str | None
    ) -> None:
        """Who the session belongs to, lower-cased, or None if the CLI did not say."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": output},
        ):
            result = await client.check_login_status()

        assert result == {"logged_in": True, "email": email}


class TestLogout:
    """Ending the CLI's session so another account can sign in."""

    async def test_logout_runs_the_command(self) -> None:
        """One run of ``logout`` for the client's region."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "Logout successful."},
        ) as run:
            await client.logout()

        run.assert_awaited_once()
        assert run.await_args is not None
        assert run.await_args.args[0] == ["logout"]

    async def test_a_failed_logout_is_not_an_error(self) -> None:
        """A session already gone is the outcome wanted; nothing is raised."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "User already logged out"},
        ):
            await client.logout()


class TestCheckHealth:
    """Test check_health method."""

    async def test_healthy(self) -> None:
        """Test when API is healthy."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "check_login_status",
            new_callable=AsyncMock,
            return_value={"logged_in": True},
        ):
            result = await client.check_health()
            assert result is True

    async def test_unhealthy(self) -> None:
        """Test when API is unhealthy."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "check_login_status",
            new_callable=AsyncMock,
            return_value={"logged_in": False},
        ):
            result = await client.check_health()
            assert result is False


# =============================================================================
# Device List Tests
# =============================================================================


class TestGetRainmakerDevices:
    """Test get_rainmaker_devices method."""

    async def test_get_devices_success(self) -> None:
        """Test successful device list retrieval."""
        client = RainMakerCLIClient()
        mock_output = json.dumps(
            {
                "node_details": [
                    {
                        "id": "ABC123456789",
                        "config": {"devices": [{"name": "Light", "type": "Light"}]},
                    }
                ]
            }
        )

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": mock_output},
        ):
            devices = await client.get_rainmaker_devices()
            assert len(devices) == 1
            assert devices[0].node_id == "ABC123456789"

    async def test_get_devices_failure(self) -> None:
        """Test failed device list retrieval."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "Network error"},
        ):
            devices = await client.get_rainmaker_devices()
            assert devices == []


class TestParseGetnodeOutput:
    """Test _parse_getnodes_output method."""

    def test_parse_json_with_node_details(self) -> None:
        """Test parsing JSON with node_details."""
        client = RainMakerCLIClient()
        output = json.dumps(
            {
                "node_details": [
                    {
                        "id": "ABC123456789",
                        "config": {"devices": [{"name": "Light", "type": "Light"}]},
                    },
                    {
                        "id": "DEF987654321",
                        "config": {"devices": [{"name": "Switch", "type": "Switch"}]},
                    },
                ]
            }
        )

        devices = client._parse_getnodes_output(output)
        assert len(devices) == 2
        assert devices[0].node_id == "ABC123456789"
        assert devices[1].node_id == "DEF987654321"

    def test_parse_json_list(self) -> None:
        """Test parsing JSON as list."""
        client = RainMakerCLIClient()
        output = json.dumps(
            [
                {
                    "id": "ABC123456789",
                    "config": {"devices": [{"name": "Light", "type": "Light"}]},
                }
            ]
        )

        devices = client._parse_getnodes_output(output)
        assert len(devices) == 1

    def test_parse_with_cli_header(self) -> None:
        """Test parsing output with CLI header."""
        client = RainMakerCLIClient()
        output = 'Using profile: global\n{"node_details": [{"id": "ABC123456789", "config": {"devices": [{"name": "Light", "type": "Light"}]}}]}'

        devices = client._parse_getnodes_output(output)
        assert len(devices) == 1

    def test_parse_text_fallback(self) -> None:
        """Test text parsing fallback."""
        client = RainMakerCLIClient()
        output = "Node ID: ABC123456789, Name: Light, Type: Light, Connected: true"

        devices = client._parse_getnodes_output(output)
        assert len(devices) == 1
        assert devices[0].node_id == "ABC123456789"


class TestParseNodeDetail:
    """Test _parse_node_detail method."""

    def test_parse_with_id(self) -> None:
        """Test parsing with 'id' field."""
        client = RainMakerCLIClient()
        detail = {
            "id": "ABC123456789",
            "config": {"devices": [{"name": "Light", "type": "Light"}]},
        }

        device = client._parse_node_detail(detail)
        assert device is not None
        assert device.node_id == "ABC123456789"
        assert device.name == "Light"

    def test_parse_with_node_id(self) -> None:
        """Test parsing with 'node_id' field."""
        client = RainMakerCLIClient()
        detail = {
            "node_id": "ABC123456789",
            "config": {"devices": [{"name": "Switch", "type": "Switch"}]},
        }

        device = client._parse_node_detail(detail)
        assert device is not None
        assert device.node_id == "ABC123456789"

    def test_parse_pure_matter_skipped(self) -> None:
        """Test that pure_matter devices are skipped."""
        client = RainMakerCLIClient()
        detail = {"id": "ABC123456789", "node_type": "pure_matter"}

        device = client._parse_node_detail(detail)
        assert device is None

    def test_parse_with_status(self) -> None:
        """Test parsing with status/connectivity."""
        client = RainMakerCLIClient()
        detail = {
            "id": "ABC123456789",
            "status": {"connectivity": {"connected": True}},
            "config": {"devices": [{"name": "Light", "type": "Light"}]},
        }

        device = client._parse_node_detail(detail)
        assert device is not None
        assert device.connected is True

    def test_parse_no_node_id(self) -> None:
        """Test parsing without node_id returns None."""
        client = RainMakerCLIClient()
        detail: dict[str, Any] = {"config": {"devices": []}}

        device = client._parse_node_detail(detail)
        assert device is None

    def test_parse_with_info_fallback(self) -> None:
        """Test parsing with info fallback when no devices."""
        client = RainMakerCLIClient()
        detail = {
            "id": "ABC123456789",
            "config": {"info": {"name": "InfoDevice", "type": "InfoType"}},
        }

        device = client._parse_node_detail(detail)
        assert device is not None
        assert device.name == "InfoDevice"

    def test_parse_matter_device_with_metadata(self) -> None:
        """Test parsing Matter device with metadata."""
        client = RainMakerCLIClient()
        detail = {
            "id": "ABC123456789",
            "is_matter": True,
            "metadata": {"Matter": {"deviceName": "MatterLight"}},
        }

        device = client._parse_node_detail(detail)
        assert device is not None
        assert device.name == "MatterLight"


class TestParseTextDeviceLine:
    """Test _parse_text_device_line method."""

    def test_parse_valid_line(self) -> None:
        """Test parsing valid text line."""
        client = RainMakerCLIClient()
        line = "Node ID: ABC123456789, Name: Light, Type: Light, Connected: true"

        device = client._parse_text_device_line(line)
        assert device is not None
        assert device.node_id == "ABC123456789"
        assert device.name == "Light"
        assert device.connected is True

    def test_parse_with_node_id_key(self) -> None:
        """Test parsing with node_id key."""
        client = RainMakerCLIClient()
        line = "node_id: ABC123456789, name: Test"

        device = client._parse_text_device_line(line)
        assert device is not None
        assert device.node_id == "ABC123456789"

    def test_parse_invalid_line(self) -> None:
        """Test parsing invalid line returns None."""
        client = RainMakerCLIClient()
        line = "Some random text without node id"

        device = client._parse_text_device_line(line)
        assert device is None


class TestExtractJsonFromOutput:
    """Test _extract_json_from_output method."""

    def test_extract_clean_json(self) -> None:
        """Test extracting clean JSON."""
        client = RainMakerCLIClient()
        output = '{"key": "value"}'

        result = client._extract_json_from_output(output)
        assert result == '{"key": "value"}'

    def test_extract_with_header(self) -> None:
        """Test extracting JSON with header."""
        client = RainMakerCLIClient()
        output = 'Using profile: global\n{"key": "value"}'

        result = client._extract_json_from_output(output)
        assert result == '{"key": "value"}'

    def test_extract_with_ansi_codes(self) -> None:
        """Test extracting JSON with ANSI codes."""
        client = RainMakerCLIClient()
        output = '\x1b[1m\x1b[32m{"key": "value"}\x1b[0m'

        result = client._extract_json_from_output(output)
        assert '{"key": "value"}' in result

    def test_extract_array(self) -> None:
        """Test extracting JSON array."""
        client = RainMakerCLIClient()
        output = 'Header\n[{"item": 1}]'

        result = client._extract_json_from_output(output)
        assert result == '[{"item": 1}]'

    def test_extract_no_json(self) -> None:
        """Test with no JSON content."""
        client = RainMakerCLIClient()
        output = "Plain text output"

        result = client._extract_json_from_output(output)
        assert result == "Plain text output"


# =============================================================================
# Device Details & Parameters Tests
# =============================================================================


class TestGetNodeDetails:
    """Test get_node_details method."""

    async def test_get_details_success(self) -> None:
        """Test successful node details retrieval."""
        client = RainMakerCLIClient()
        mock_output = json.dumps({"devices": [{"name": "Light"}]})

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": mock_output},
        ):
            result = await client.get_node_details("ABC123")
            assert result is not None
            assert "devices" in result

    async def test_get_details_failure(self) -> None:
        """Test failed node details retrieval."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "Not found"},
        ):
            result = await client.get_node_details("ABC123")
            assert result is None

    async def test_get_details_invalid_json(self) -> None:
        """Test with invalid JSON response."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "invalid json"},
        ):
            result = await client.get_node_details("ABC123")
            assert result is None


class TestGetParams:
    """Test get_params method."""

    async def test_get_params_success(self) -> None:
        """Test successful params retrieval."""
        client = RainMakerCLIClient()
        mock_output = json.dumps({"Light": {"power": True}})

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": mock_output},
        ):
            result = await client.get_params("ABC123")
            assert result is not None
            assert result["Light"]["power"] is True

    async def test_get_params_failure(self) -> None:
        """Test failed params retrieval."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "Error"},
        ):
            result = await client.get_params("ABC123")
            assert result is None

    async def test_get_params_invalid_json(self) -> None:
        """Test with invalid JSON response returns None."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "not json"},
        ):
            result = await client.get_params("ABC123")
            assert result is None


class TestSetParams:
    """Test set_params method."""

    async def test_set_params_success(self) -> None:
        """Test successful params setting."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "OK"},
        ) as mock_run:
            result = await client.set_params("ABC123", {"Light": {"power": True}})
            assert result is True
            # Verify JSON was passed
            call_args = mock_run.call_args[0][0]
            assert "--data" in call_args

    async def test_set_params_failure(self) -> None:
        """Test failed params setting."""
        client = RainMakerCLIClient()

        with patch.object(
            client,
            "_run_cli_async",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "Failed"},
        ):
            result = await client.set_params("ABC123", {"Light": {"power": True}})
            assert result is False


# =============================================================================
# BLE Provisioning Tests
# =============================================================================


class TestProvisionDevice:
    """Test provision_device method."""

    async def test_provision_success(self) -> None:
        """Test successful provisioning."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "output": "Node ABC123456789 provisioned\nadded to your account",
                },
            ),
        ):
            result = await client.provision_device(
                device_name="PROV_123456",
                ssid="TestSSID",
                wifi_password="password123",
            )
            assert result["success"] is True
            assert result["node_id"] == "ABC123456789"
            assert result["bound"] is True

    async def test_provision_partial_success(self) -> None:
        """Test partial provisioning success (WiFi OK but not bound)."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={
                    "success": False,
                    "output": "Wi-Fi Provisioning Successful",
                    "error": "mapping timeout",
                },
            ),
        ):
            result = await client.provision_device(
                device_name="PROV_123456",
                ssid="TestSSID",
                wifi_password="password123",
            )
            assert result["success"] is True
            assert result["bound"] is False

    async def test_provision_failure(self) -> None:
        """Test provisioning failure."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={"success": False, "error": "BLE connection failed"},
            ),
        ):
            result = await client.provision_device(
                device_name="PROV_123456",
                ssid="TestSSID",
                wifi_password="password123",
            )
            assert result["success"] is False
            assert "BLE connection failed" in result["error"]

    async def test_provision_with_pop(self) -> None:
        """Test provisioning with PoP."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "output": "Node ABC123456789 provisioned",
                },
            ) as mock_run,
        ):
            await client.provision_device(
                device_name="PROV_123456",
                ssid="TestSSID",
                wifi_password="password123",
                pop="abcd1234",
            )
            call_args = mock_run.call_args[0][0]
            assert "--pop" in call_args
            assert "abcd1234" in call_args
            assert "--sec_ver" in call_args
            assert "1" in call_args

    async def test_provision_invalid_config_error(self) -> None:
        """Test provisioning failure with 'Invalid configuration' from CLI."""
        client = RainMakerCLIClient()

        with (
            patch.object(
                client,
                "_ensure_profile_active",
                new_callable=AsyncMock,
            ),
            patch.object(
                client,
                "_run_cli_async",
                new_callable=AsyncMock,
                return_value={
                    "success": False,
                    "error": "[ERROR]:Provisioning failed: "
                    "Invalid configuration. Please login again",
                },
            ),
        ):
            result = await client.provision_device(
                device_name="PROV_123456",
                ssid="TestSSID",
                wifi_password="password123",
            )
            assert result["success"] is False
            assert "Login session invalid" in result["error"]


class TestExtractNodeId:
    """Test _extract_node_id method."""

    def test_extract_from_provisioned_message(self) -> None:
        """Test extracting node_id from 'Node XXX provisioned' message."""
        client = RainMakerCLIClient()
        output = "Node ABC123456789 provisioned successfully"

        node_id = client._extract_node_id(output)
        assert node_id == "ABC123456789"

    def test_extract_from_node_id_line(self) -> None:
        """Test extracting node_id from 'node_id: XXX' line."""
        client = RainMakerCLIClient()
        output = "node_id: DEF987654321\nOther info"

        node_id = client._extract_node_id(output)
        assert node_id == "DEF987654321"

    def test_extract_not_found(self) -> None:
        """Test when node_id not found."""
        client = RainMakerCLIClient()
        output = "Some random output"

        node_id = client._extract_node_id(output)
        assert node_id is None

    def test_extract_case_insensitive(self) -> None:
        """Test case insensitive extraction."""
        client = RainMakerCLIClient()
        output = "node ABC123456789 PROVISIONED"

        node_id = client._extract_node_id(output)
        assert node_id == "ABC123456789"

    def test_extract_longer_node_id(self) -> None:
        """Test extracting node_id longer than 12 hex characters."""
        client = RainMakerCLIClient()
        output = "Node AABBCCDDEEFF001122 provisioned successfully"

        node_id = client._extract_node_id(output)
        assert node_id == "AABBCCDDEEFF001122"


class TestCheckBound:
    """Test _check_bound method."""

    def test_bound_added_to_account(self) -> None:
        """Test detecting 'added to your account' indicator."""
        client = RainMakerCLIClient()
        output = "Device added to your account successfully"

        assert client._check_bound(output) is True

    def test_bound_mapping_confirmed(self) -> None:
        """Test detecting 'mapping status: confirmed' indicator."""
        client = RainMakerCLIClient()
        output = "Mapping status: confirmed"

        assert client._check_bound(output) is True

    def test_not_bound(self) -> None:
        """Test when not bound."""
        client = RainMakerCLIClient()
        output = "Provisioning complete"

        assert client._check_bound(output) is False
