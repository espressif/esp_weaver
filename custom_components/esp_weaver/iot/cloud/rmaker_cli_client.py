# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP RainMaker Cloud Client using esp-rainmaker-cli commands.

This module provides a client for communicating with ESP RainMaker cloud
by invoking esp-rainmaker-cli commands via subprocess.

Features:
- Login/logout management
- Device provisioning via BLE (with automatic node mapping)
- Device parameter get/set
- Automatic profile (region) support
- Auto-install esp-rainmaker-cli on first use

Dependencies:
- esp-secure-cert-tool>=2.0.0 (declared in manifest.json, no cryptography conflict)
- esp-rainmaker-cli (auto-installed with --no-deps to avoid cryptography conflict)
"""

import asyncio
from dataclasses import dataclass
from functools import partial
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

_LOGGER = logging.getLogger(__name__)

# CLI command name
CLI_CMD = "esp-rainmaker-cli"

# Flag to track if installation has been attempted this session
_install_attempted = False

# Pre-compiled regex for stripping ANSI escape codes from CLI output
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _check_cli_installed() -> bool:
    """Check if esp-rainmaker-cli is installed."""
    return shutil.which(CLI_CMD) is not None


_proto_fix_env: dict[str, str] | None = None


def _get_proto_fix_env() -> dict[str, str]:
    """Get environment variables to fix proto module conflict in subprocess.

    Home Assistant uses proto-plus which provides a 'proto' module.
    esp-rainmaker-cli uses rmaker_tools which has its own internal 'proto' directory.
    When 'import proto' is executed, Python finds proto-plus's proto instead.

    This function returns env vars to prepend rmaker_tools/rmaker_prov to PYTHONPATH,
    so the subprocess finds the correct proto module without affecting the main process.
    The result is cached after the first call.

    Returns:
        Dict with environment variables including modified PYTHONPATH.
    """
    global _proto_fix_env  # noqa: PLW0603  # pylint: disable=global-statement
    if _proto_fix_env is not None:
        return _proto_fix_env

    env = os.environ.copy()

    try:
        import rmaker_tools  # noqa: PLC0415

        rmaker_tools_dir = Path(rmaker_tools.__file__).parent
        proto_path = str(rmaker_tools_dir / "rmaker_prov")
        current_pythonpath = env.get("PYTHONPATH", "")
        if current_pythonpath:
            env["PYTHONPATH"] = os.pathsep.join([proto_path, current_pythonpath])
        else:
            env["PYTHONPATH"] = proto_path

    except ImportError:
        _LOGGER.debug("rmaker_tools not installed, proto fix not needed")
    except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Failed to get proto fix env: %s", err)

    _proto_fix_env = env
    return env


def _install_cli() -> bool:
    """Install esp-rainmaker-cli with --no-deps to avoid cryptography conflict.

    The esp-rainmaker-cli package requires esp-secure-cert-tool==1.0.1 which
    depends on cryptography==36.0.0. This conflicts with Home Assistant's
    cryptography>=46.0.2.

    Solution: Install esp-rainmaker-cli with --no-deps, and use the newer
    esp-secure-cert-tool>=2.0.0 (declared in manifest.json) which doesn't
    have the cryptography version conflict.

    Returns:
        True if installation succeeded, False otherwise.
    """
    global _install_attempted  # noqa: PLW0603  # pylint: disable=global-statement
    if _install_attempted:
        return _check_cli_installed()

    _install_attempted = True
    _LOGGER.info("Installing %s (first time setup)...", CLI_CMD)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", CLI_CMD, "--no-deps", "-q"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _LOGGER.error("Installation of %s timed out", CLI_CMD)
        return False
    except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        _LOGGER.error("Failed to install %s: %s", CLI_CMD, err)
        return False

    if result.returncode == 0:
        _LOGGER.info("%s installed successfully", CLI_CMD)
        return True

    _LOGGER.error("Failed to install %s: %s", CLI_CMD, result.stderr)
    return False


def _ensure_cli_available() -> bool:
    """Ensure esp-rainmaker-cli is available, installing if necessary.

    Returns:
        True if CLI is available, False otherwise.
    """
    if _check_cli_installed():
        return True
    return _install_cli()


@dataclass
class CloudDeviceInfo:
    """Information about a cloud device."""

    node_id: str
    name: str
    connected: bool = False


@dataclass
class BLEDeviceInfo:
    """Information about a BLE device for provisioning."""

    name: str


class RainMakerCLIClient:
    """Client for ESP RainMaker using esp-rainmaker-cli commands.

    All operations are performed by invoking CLI commands via subprocess.
    """

    def __init__(self, region: str = "global") -> None:
        """Initialize the CLI client.

        Args:
            region: Region for API ('global' or 'china').
        """
        self._region = region
        self._profile = "china" if region == "china" else "global"

    def _run_cli(
        self,
        args: list[str],
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Run CLI command and return result.

        Args:
            args: Command arguments (without the main command).
            timeout: Command timeout in seconds.

        Returns:
            Dict with 'success', 'output', and optionally 'error'.
        """
        if not _ensure_cli_available():
            return {
                "success": False,
                "error": f"{CLI_CMD} not available and auto-install failed",
            }

        cmd = [CLI_CMD, *args, "--profile", self._profile]
        env = _get_proto_fix_env()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return {"success": False, "error": str(err)}

        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode == 0:
            return {"success": True, "output": output}
        return {"success": False, "output": output, "error": error or output}

    async def _run_cli_async(
        self,
        args: list[str],
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Run CLI command asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._run_cli, args, timeout),
        )

    # =========================================================================
    # Authentication (认证)
    # =========================================================================

    async def _ensure_profile_active(self) -> None:
        """Ensure the CLI's active profile matches our target profile.

        Only needed for non-global profiles.  The CLI's ``provision`` command
        creates ``Config()`` without ``profile_override``, so it falls back to
        ``current_profile`` file (default: 'global').  For 'china' profile
        this would be wrong, so we write the file explicitly.
        """
        if self._profile == "global":
            return

        def _write_profile() -> None:
            profiles_dir = Path.home() / ".espressif" / "rainmaker" / "profiles"
            profiles_dir.mkdir(parents=True, exist_ok=True)
            (profiles_dir / "current_profile").write_text(
                self._profile, encoding="utf-8"
            )

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_profile)
            _LOGGER.debug("CLI active profile set to '%s'", self._profile)
        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "Failed to set CLI active profile to '%s': %s",
                self._profile,
                err,
            )

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Login to RainMaker using CLI.

        Args:
            email: User email.
            password: User password.

        Returns:
            Dict with login result.

        Note:
            Password is passed via --password flag to use the CLI's direct
            (programmatic) login path, bypassing the interactive prompt.
        """
        await self._ensure_profile_active()

        result = await self._run_cli_async(
            ["login", "--email", email, "--password", password],
            timeout=60,
        )

        if not result.get("success"):
            error = result.get("error", "Login failed")
            _LOGGER.error("RainMaker login failed for email=%s", email[:3] + "***")
            return {"success": False, "error": error}

        # CLI login may exit 0 even on failure (same bug as getuserinfo).
        # Verify by checking actual login status.
        verify = await self.check_login_status()
        if not verify.get("logged_in"):
            _LOGGER.error(
                "Login command succeeded but verification failed for profile '%s'",
                self._profile,
            )
            return {
                "success": False,
                "error": "Login failed for this region. "
                "Please check your account is registered on the selected server.",
            }

        _LOGGER.info("RainMaker login successful for profile '%s'", self._profile)
        return {"success": True, "email": email, "region": self._region}

    async def check_login_status(self) -> dict[str, Any]:
        """Check if logged in by trying to get user info.

        Note: The CLI's ``getuserinfo`` command catches exceptions internally
        and may exit with returncode 0 even when NOT logged in.  We must
        inspect the output text to detect "not logged in" indicators.
        """
        result = await self._run_cli_async(["getuserinfo"], timeout=10)

        output = result.get("output", "")
        error = result.get("error", "")
        combined = (output + " " + error).lower()

        not_logged_in_indicators = [
            "not logged in",
            "unauthorized",
            "invalid configuration",
            "please login",
            "login first",
            "no user",
            "no valid tokens",
        ]

        for indicator in not_logged_in_indicators:
            if indicator in combined:
                _LOGGER.debug(
                    "check_login_status: detected '%s' in CLI output", indicator
                )
                return {"logged_in": False, "error": "Not logged in"}

        if result.get("success"):
            return {"logged_in": True}

        return {"logged_in": False, "error": result.get("error", "Unknown error")}

    async def check_health(self) -> bool:
        """Check if RainMaker API is accessible."""
        status = await self.check_login_status()
        return bool(status.get("logged_in", False))

    async def get_node_connectivity(self, node_id: str) -> bool | None:
        """Check if a specific node is connected to the cloud.

        Uses ``getnodedetails <nodeid> --raw`` to query a single node
        instead of fetching the full device list.

        Returns:
            True if connected, False if disconnected, None if node not found.
        """
        result = await self._run_cli_async(
            ["getnodedetails", node_id, "--raw"], timeout=15
        )

        if not result.get("success"):
            _LOGGER.debug(
                "get_node_connectivity failed for %s: %s",
                node_id,
                result.get("error"),
            )
            return None

        output = result.get("output", "")
        json_output = self._extract_json_from_output(output)
        try:
            data = json.loads(json_output)
        except json.JSONDecodeError:
            return None

        # Response is {"node_details": [{...}]} for single-node query
        details = data.get("node_details", [data] if isinstance(data, dict) else data)
        if not details:
            return None

        detail = details[0] if isinstance(details, list) else details
        return bool(
            detail.get("status", {}).get("connectivity", {}).get("connected", False)
        )

    # =========================================================================
    # Device List (设备列表)
    # =========================================================================

    async def get_rainmaker_devices(self) -> list[CloudDeviceInfo]:
        """Get list of RainMaker devices."""
        result = await self._run_cli_async(["getnodedetails", "--raw"], timeout=30)

        if not result.get("success"):
            _LOGGER.error("Failed to get devices: %s", result.get("error"))
            return []

        return self._parse_getnodes_output(result.get("output", ""))

    def _parse_getnodes_output(self, output: str) -> list[CloudDeviceInfo]:
        """Parse getnodes command output."""
        devices: list[CloudDeviceInfo] = []

        json_output = self._extract_json_from_output(output)

        try:
            data = json.loads(json_output)
            if isinstance(data, dict) and "node_details" in data:
                devices.extend(
                    d
                    for detail in data["node_details"]
                    if (d := self._parse_node_detail(detail))
                )
            elif isinstance(data, list):
                devices.extend(
                    d for detail in data if (d := self._parse_node_detail(detail))
                )
        except json.JSONDecodeError as e:
            _LOGGER.debug("JSON parse failed: %s. Output: %s", e, json_output[:500])
            for line in output.split("\n"):
                if "Node ID:" in line or "node_id" in line.lower():
                    device = self._parse_text_device_line(line)
                    if device:
                        devices.append(device)

        return devices

    def _parse_node_detail(self, detail: dict[str, Any]) -> CloudDeviceInfo | None:
        """Parse node detail dict into CloudDeviceInfo."""
        node_id = detail.get("id") or detail.get("node_id")
        if not node_id:
            return None

        node_type = detail.get("node_type", "traditional_rainmaker")
        if node_type == "pure_matter":
            return None

        is_matter = detail.get("is_matter", False)
        connected = (
            detail.get("status", {}).get("connectivity", {}).get("connected", False)
        )

        device_name = f"RainMaker Device {node_id[:8]}"

        # Priority: config.devices[0].name > config.info.name > default
        if config := detail.get("config"):
            devices_list = config.get("devices", [])
            if devices_list and isinstance(devices_list, list):
                first_device = devices_list[0]
                device_name = first_device.get("name", device_name)
            else:
                info = config.get("info", {})
                device_name = info.get("name", device_name)
        elif is_matter and (metadata := detail.get("metadata")):
            matter_data = metadata.get("Matter", {})
            device_name = matter_data.get("deviceName", device_name)

        return CloudDeviceInfo(
            node_id=node_id,
            name=device_name,
            connected=connected,
        )

    def _parse_text_device_line(self, line: str) -> CloudDeviceInfo | None:
        """Parse a text line into CloudDeviceInfo (fallback parser)."""
        parts = {}
        for segment in line.split(","):
            if ":" in segment:
                key, value = segment.split(":", 1)
                parts[key.strip().lower()] = value.strip()

        node_id = parts.get("node id") or parts.get("node_id")
        if not node_id:
            return None

        return CloudDeviceInfo(
            node_id=node_id,
            name=parts.get("name", f"Device {node_id[:8]}"),
            connected=parts.get("connected", "").lower() == "true",
        )

    def _extract_json_from_output(self, output: str) -> str:
        """Extract JSON content from CLI output, skipping non-JSON lines.

        CLI output may include:
        - Lines like "Using profile: global" before JSON
        - ANSI color codes like [1m, [32m, [0m

        This method removes ANSI codes and finds the first '{' or '['.
        """
        clean_output = _ANSI_ESCAPE_RE.sub("", output)

        json_start = -1
        for i, char in enumerate(clean_output):
            if char in "{[":
                json_start = i
                break

        if json_start == -1:
            return clean_output  # No JSON found, return cleaned output

        return clean_output[json_start:]

    # =========================================================================
    # Device Details & Parameters (设备详情和参数)
    # =========================================================================

    async def get_node_details(self, node_id: str) -> dict[str, Any] | None:
        """Get details for a specific node."""
        result = await self._run_cli_async(
            ["getnodeconfig", node_id],
            timeout=30,
        )

        if not result.get("success"):
            _LOGGER.error("Failed to get node details: %s", result.get("error"))
            return None

        output = result.get("output", "{}")
        json_output = self._extract_json_from_output(output)
        try:
            data: dict[str, Any] = json.loads(json_output)
        except json.JSONDecodeError as e:
            _LOGGER.warning(
                "getnodeconfig JSON parse failed for %s: %s",
                node_id,
                e,
            )
            return None
        return data

    async def get_params(self, node_id: str) -> dict[str, Any] | None:
        """Get parameters for a node."""
        result = await self._run_cli_async(
            ["getparams", node_id],
            timeout=15,
        )

        if not result.get("success"):
            _LOGGER.error("Failed to get params: %s", result.get("error"))
            return None

        output = result.get("output", "{}")
        json_output = self._extract_json_from_output(output)
        try:
            data: dict[str, Any] = json.loads(json_output)
        except json.JSONDecodeError:
            _LOGGER.warning("getparams JSON parse failed for %s", node_id)
            return None
        return data

    async def set_params(self, node_id: str, params: dict[str, Any]) -> bool:
        """Set parameters for a node."""
        params_json = json.dumps(params)
        result = await self._run_cli_async(
            ["setparams", node_id, "--data", params_json],
            timeout=15,
        )

        if not result.get("success"):
            _LOGGER.error("Failed to set params: %s", result.get("error"))
            return False

        return True

    # =========================================================================
    # BLE Provisioning (BLE 配网)
    # =========================================================================

    async def provision_device(
        self,
        device_name: str,
        ssid: str,
        wifi_password: str,
        pop: str = "",
        transport: str = "ble",
    ) -> dict[str, Any]:
        """Provision an ESP device with WiFi credentials and add to account.

        The CLI handles the complete provisioning flow:
        1. Connect to device via BLE
        2. Send WiFi credentials
        3. Wait for device to connect to WiFi
        4. Wait for node mapping confirmation (device added to account)

        Args:
            device_name: BLE device name (e.g., "PROV_XXXXXX").
            ssid: WiFi SSID.
            wifi_password: WiFi password.
            pop: Proof of Possession (if required).
            transport: Transport type ("ble" or "softap").

        Returns:
            Dict with provisioning result:
            - success: True if provisioning succeeded
            - node_id: Device node ID
            - bound: True if device was added to user account
            - message: Status message
        """
        await self._ensure_profile_active()

        cmd = [
            "provision",
            "--transport",
            transport,
            "--ssid",
            ssid,
            "--passphrase",
            wifi_password,
            "--device_name",
            device_name,
            "--sec_ver",
            "1",
            "--no-retry",
        ]

        if pop:
            cmd.extend(["--pop", pop])

        _LOGGER.info(
            "Provisioning device: %s (transport=%s, ssid=%s)",
            device_name,
            transport,
            ssid,
        )
        result = await self._run_cli_async(cmd, timeout=180)

        output = result.get("output", "") + "\n" + result.get("error", "")
        _LOGGER.debug("Provision CLI output: %s", output[:2000])

        cli_success = result.get("success")
        wifi_success = not cli_success and (
            "Wi-Fi Provisioning Successful" in output
            or "provisioned successfully" in output.lower()
        )

        if cli_success or wifi_success:
            node_id = self._extract_node_id(output)
            bound = self._check_bound(output)
            if cli_success:
                _LOGGER.info(
                    "Provisioning successful: node_id=%s, bound=%s", node_id, bound
                )
            else:
                _LOGGER.warning(
                    "Provisioning partially successful: WiFi OK, bound=%s", bound
                )
            return {
                "success": True,
                "device_name": device_name,
                "node_id": node_id,
                "bound": bound,
                "message": "Device provisioned"
                + (" and added to account" if bound else ""),
            }

        error = result.get("error", "") or "Provisioning failed"

        login_error_indicators = [
            "invalid configuration",
            "please login",
            "not logged in",
            "no valid access token",
        ]
        output_lower = output.lower()
        if any(ind in output_lower for ind in login_error_indicators):
            _LOGGER.error("Provisioning failed due to login issue: %s", error)
            return {
                "success": False,
                "error": "Login session invalid. Please remove the integration, "
                "re-login and try again.",
            }

        _LOGGER.error("Provisioning failed: %s", error)
        return {"success": False, "error": error}

    def _extract_node_id(self, output: str) -> str | None:
        """Extract node_id from CLI output."""
        match = re.search(r"Node\s+([A-F0-9]{6,})\s+provisioned", output, re.IGNORECASE)
        if match:
            return match.group(1)

        for line in output.split("\n"):
            if "node_id" in line.lower() or "node id" in line.lower():
                parts = line.split(":")
                if len(parts) >= 2:
                    node_id = parts[-1].strip().strip('"').strip("'")
                    if node_id and len(node_id) >= 6:
                        return node_id
        return None

    def _check_bound(self, output: str) -> bool:
        """Check if device was added to account."""
        indicators = [
            "added to your account",
            "mapping status: confirmed",
        ]
        output_lower = output.lower()
        return any(ind in output_lower for ind in indicators)
