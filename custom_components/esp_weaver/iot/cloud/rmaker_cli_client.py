# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Cloud control, driven by shelling out to esp-rainmaker-cli.

Cloud control is not supported in this release: ``CLOUD_CONTROL_ENABLED`` is
False, so the config flow does not offer it and nothing here is a declared
dependency.

RainMaker has no Python client Home Assistant can depend on, so every operation
is a CLI subprocess and the CLI's own token store keeps the login.
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
import sysconfig
import threading
from typing import Any, Final

from ..specs.keys import RAINMAKER_REGION_CHINA, RAINMAKER_REGION_GLOBAL

_LOGGER = logging.getLogger(__name__)

CLI_CMD: Final[str] = "esp-rainmaker-cli"
PASSWORD_ENV: Final[str] = "ESP_RAINMAKER_PASSWORD"

# What to tell a user whose environment we cannot install into.
INSTALL_INSTRUCTIONS: Final[str] = (
    f"Cloud control needs {CLI_CMD}, which is not installed and cannot be "
    f"installed into this environment. Install it where Home Assistant runs "
    f"with: pip install {CLI_CMD} --no-deps"
)

# One install attempt per run, and one at a time: the first cloud device to be
# set up would otherwise race every other one into a duplicate pip install.
_install_lock = threading.Lock()
_install_attempted = False

_ANSI_ESCAPE_RE: Final = re.compile(r"\x1b\[[0-9;]*m")

# Provisioning waits for a person at a phone and a device joining a network,
# both of which are slow, and there is no second attempt if it is cut short.
PROVISION_TIMEOUT: Final[int] = 180
INSTALL_TIMEOUT: Final[int] = 120
LOGIN_TIMEOUT: Final[int] = 60
DEFAULT_CLI_TIMEOUT: Final[int] = 30
NODE_QUERY_TIMEOUT: Final[int] = 15
STATUS_TIMEOUT: Final[int] = 10

# Everything the CLI says when the stored login has gone stale.
LOGIN_FAILURE_SIGNS: Final[tuple[str, ...]] = (
    "invalid configuration",
    "please login",
    "login first",
    "not logged in",
    "unauthorized",
    "no user",
    "no valid access token",
    "no valid tokens",
)


def _says_login_expired(text: str) -> bool:
    """Say whether some CLI output is really about the login rather than the task."""
    lowered = text.lower()
    return any(sign in lowered for sign in LOGIN_FAILURE_SIGNS)


# How ``getuserinfo`` names the account: ``User Name (email): a@b.c``.
_SIGNED_IN_EMAIL_RE: Final = re.compile(
    r"^\s*user\s*name(?:\s*\(email\))?\s*:\s*(?P<email>\S+)",
    re.IGNORECASE | re.MULTILINE,
)


def _signed_in_email(output: str) -> str | None:
    """Return the account ``getuserinfo`` says it is signed in as, if it names one."""
    match = _SIGNED_IN_EMAIL_RE.search(_ANSI_ESCAPE_RE.sub("", output))
    return match.group("email").lower() if match else None


def _says_wifi_succeeded(output: str) -> bool:
    """Say whether the CLI reported the WiFi half as done, in either wording."""
    return (
        "Wi-Fi Provisioning Successful" in output
        or "provisioned successfully" in output.lower()
    )


def _check_cli_installed() -> bool:
    """Say whether the CLI is on PATH."""
    return shutil.which(CLI_CMD) is not None


_proto_fix_env: dict[str, str] | None = None


def _get_proto_fix_env() -> dict[str, str]:
    """Return the environment the CLI needs to import its own ``proto`` package.

    Home Assistant's proto-plus owns the name ``proto``; the CLI's rmaker_tools
    goes first on PYTHONPATH for the subprocess.
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

    _proto_fix_env = env
    return env


def _install_cli() -> bool:
    """Install the CLI once per run, with ``--no-deps``.

    Its pins would undo Home Assistant's ``cryptography``; what it needs is
    declared in manifest.json instead.
    """
    global _install_attempted  # noqa: PLW0603  # pylint: disable=global-statement

    # Held across the install, so a second device setting up waits for the
    # answer instead of starting its own pip.
    with _install_lock:
        if _install_attempted:
            return _check_cli_installed()
        _install_attempted = True

        if not _environment_is_writable():
            # pip would spend the timeout failing with a permissions message.
            _LOGGER.error("%s", INSTALL_INSTRUCTIONS)
            return False

        _LOGGER.info("Installing %s (first time setup)...", CLI_CMD)

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", CLI_CMD, "--no-deps", "-q"],
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _LOGGER.error("Installation of %s timed out", CLI_CMD)
            return False
        except OSError as err:
            _LOGGER.error("Failed to install %s: %s", CLI_CMD, err)
            return False

        if result.returncode == 0:
            _LOGGER.info("%s installed successfully", CLI_CMD)
            return True

        _LOGGER.error(
            "Failed to install %s: %s. %s",
            CLI_CMD,
            result.stderr,
            INSTALL_INSTRUCTIONS,
        )
        return False


def _environment_is_writable() -> bool:
    """Return True if this Python environment can have packages added to it.

    Home Assistant is often a read-only container; asking first turns a
    two-minute failure into an immediate message.
    """
    target = Path(sysconfig.get_path("purelib"))
    return os.access(target, os.W_OK)


def _ensure_cli_available() -> bool:
    """Return whether the CLI can be run, installing it if this is the first ask."""
    return _check_cli_installed() or _install_cli()


@dataclass
class CloudDeviceInfo:
    """One node as the RainMaker account lists it."""

    node_id: str
    name: str
    connected: bool = False


class RainMakerCLIClient:
    """Every RainMaker operation, each one a run of the CLI."""

    def __init__(self, region: str = RAINMAKER_REGION_GLOBAL) -> None:
        """Bind this client to one region, which the CLI calls a profile."""
        self._region = region
        self._profile = (
            RAINMAKER_REGION_CHINA
            if region == RAINMAKER_REGION_CHINA
            else RAINMAKER_REGION_GLOBAL
        )

    def _run_cli(
        self,
        args: list[str],
        timeout: int = DEFAULT_CLI_TIMEOUT,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one CLI command, returning its success, output and error."""
        if not _ensure_cli_available():
            return {"success": False, "error": INSTALL_INSTRUCTIONS}

        cmd = [CLI_CMD, *args, "--profile", self._profile]
        env = {**_get_proto_fix_env(), **(extra_env or {})}

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
        except (OSError, ValueError) as err:
            return {"success": False, "error": str(err)}

        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode == 0:
            return {"success": True, "output": output}
        return {"success": False, "output": output, "error": error or output}

    async def _run_cli_async(
        self,
        args: list[str],
        timeout: int = DEFAULT_CLI_TIMEOUT,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one CLI command off the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._run_cli, args, timeout, extra_env),
        )

    async def _ensure_profile_active(self) -> None:
        """Write the region into the ``current_profile`` file.

        ``provision`` is the one command without a profile override, so it
        reads that file; written for every region so an earlier China client
        cannot leave it wrong.
        """

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
        except OSError as err:
            _LOGGER.warning(
                "Failed to set CLI active profile to '%s': %s",
                self._profile,
                err,
            )

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Log the CLI into one RainMaker account, and confirm that it took.

        The password travels in the environment, not on the command line.
        """
        await self._ensure_profile_active()

        result = await self._run_cli_async(
            ["login", "--email", email],
            timeout=LOGIN_TIMEOUT,
            extra_env={PASSWORD_ENV: password},
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
        """Ask the CLI who it is logged in as.

        Read from the output, not the exit code: ``getuserinfo`` exits 0 even
        when it reports nobody logged in.

        Returns:
            ``logged_in``, and the session's ``email`` when there is one.
        """
        result = await self._run_cli_async(["getuserinfo"], timeout=STATUS_TIMEOUT)

        output = result.get("output", "")
        error = result.get("error", "")

        if _says_login_expired(f"{output} {error}"):
            _LOGGER.debug("check_login_status: CLI output reports no valid login")
            return {"logged_in": False, "error": "Not logged in"}

        if result.get("success"):
            return {"logged_in": True, "email": _signed_in_email(output)}

        return {"logged_in": False, "error": result.get("error", "Unknown error")}

    async def logout(self) -> None:
        """End the CLI's session for this region, if it has one.

        ``login`` prompts on the terminal about an existing session, and there
        is no terminal here. Failure is not an error.
        """
        result = await self._run_cli_async(["logout"], timeout=STATUS_TIMEOUT)
        if not result.get("success"):
            _LOGGER.debug("RainMaker logout reported: %s", result.get("error"))

    async def check_health(self) -> bool:
        """Check if RainMaker API is accessible."""
        status = await self.check_login_status()
        return bool(status.get("logged_in", False))

    async def get_node_connectivity(self, node_id: str) -> bool | None:
        """Ask about one node rather than fetching the whole account's list.

        Returns:
            True or False for the node's connectivity; None if nothing is known
            (node missing, login expired, unreadable answer - the CLI reports
            all three the same way).
        """
        result = await self._run_cli_async(
            ["getnodedetails", node_id, "--raw"], timeout=NODE_QUERY_TIMEOUT
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

        # A single-node query answers {"node_details": [{...}]}; older CLIs
        # answer with the detail itself, or a list of them.
        details = data.get("node_details", [data]) if isinstance(data, dict) else data
        if not isinstance(details, list) or not details:
            return None

        detail = details[0]
        if not isinstance(detail, dict):
            return None
        return bool(
            detail.get("status", {}).get("connectivity", {}).get("connected", False)
        )

    async def get_rainmaker_devices(self) -> list[CloudDeviceInfo]:
        """Get list of RainMaker devices."""
        result = await self._run_cli_async(
            ["getnodedetails", "--raw"], timeout=DEFAULT_CLI_TIMEOUT
        )

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
            details = data.get("node_details", []) if isinstance(data, dict) else data
            if isinstance(details, list):
                devices.extend(
                    d
                    for detail in details
                    if isinstance(detail, dict)
                    and (d := self._parse_node_detail(detail))
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

        # Not a vendor's name, and not the node id: see OTHER_DEVICE_LABEL.
        device_name = "Other device"

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
        """Extract the JSON from CLI output, past ANSI codes and prefix lines."""
        clean_output = _ANSI_ESCAPE_RE.sub("", output)

        json_start = -1
        for i, char in enumerate(clean_output):
            if char in "{[":
                json_start = i
                break

        if json_start == -1:
            return clean_output  # No JSON found, return cleaned output

        return clean_output[json_start:]

    async def get_node_details(self, node_id: str) -> dict[str, Any] | None:
        """Get details for a specific node."""
        result = await self._run_cli_async(
            ["getnodeconfig", node_id],
            timeout=DEFAULT_CLI_TIMEOUT,
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
            timeout=NODE_QUERY_TIMEOUT,
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
            timeout=NODE_QUERY_TIMEOUT,
        )

        if not result.get("success"):
            _LOGGER.error("Failed to set params: %s", result.get("error"))
            return False

        return True

    async def provision_device(
        self,
        device_name: str,
        ssid: str,
        wifi_password: str,
        pop: str = "",
        transport: str = "ble",
    ) -> dict[str, Any]:
        """Provision a device over BLE with WiFi credentials and add it to the account.

        Known limits of the CLI: the WiFi password goes on its command line
        (visible to other processes while the run lasts; there is no terminal
        to prompt on), and it opens the Bluetooth adapter itself, uncoordinated
        with Home Assistant's Bluetooth integration.

        Args:
            device_name: BLE device name, e.g. ``PROV_XXXXXX``.
            pop: Proof of possession, if the device needs one.
            transport: ``ble`` or ``softap``.

        Returns:
            ``success``, ``node_id``, ``bound`` (added to the account) and
            ``message``.
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
        result = await self._run_cli_async(cmd, timeout=PROVISION_TIMEOUT)

        output = result.get("output", "") + "\n" + result.get("error", "")
        _LOGGER.debug("Provision CLI output: %s", output[:2000])

        # The CLI can fail overall and still have got the device onto the
        # WiFi, which cannot be repeated; read its output too.
        cli_success = result.get("success")
        wifi_success = not cli_success and _says_wifi_succeeded(output)

        if cli_success or wifi_success:
            return self._provisioned(device_name, output, unbound=not cli_success)

        return self._provision_failed(result, output)

    def _provisioned(
        self, device_name: str, output: str, *, unbound: bool
    ) -> dict[str, Any]:
        """Report a device that is on the WiFi, bound to the account or not."""
        node_id = self._extract_node_id(output)
        bound = self._check_bound(output)

        if unbound:
            _LOGGER.warning(
                "Provisioning partially successful: WiFi OK, bound=%s", bound
            )
        else:
            _LOGGER.info(
                "Provisioning successful: node_id=%s, bound=%s", node_id, bound
            )

        return {
            "success": True,
            "device_name": device_name,
            "node_id": node_id,
            "bound": bound,
            "message": "Device provisioned"
            + (" and added to account" if bound else ""),
        }

    @staticmethod
    def _provision_failed(result: dict[str, Any], output: str) -> dict[str, Any]:
        """Report a failed run, telling an expired login apart from a device problem."""
        error = result.get("error", "") or "Provisioning failed"

        if _says_login_expired(output):
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
