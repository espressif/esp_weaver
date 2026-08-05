# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Config flow for ESP-Weaver integration.

This module handles the configuration flow for adding ESP devices to Home Assistant,
including device discovery, security detection, PoP authentication, and cloud control.
"""

import asyncio
import logging
from typing import Any, Literal

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    ABORT_NO_DEVICES,
    ABORT_NO_NEW_DEVICES,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_DEVICE,
    ERROR_INVALID_POP,
    ERROR_POP_REQUIRED,
    ERROR_PROVISIONING_FAILED,
    ERROR_RAINMAKER_LOGIN_FAILED,
    ERROR_RAINMAKER_NOT_LOGGED_IN,
    ERROR_WIFI_SSID_REQUIRED,
    FIELD_SELECTED_DEVICE,
    PLACEHOLDER_DEVICE_COUNT,
    PLACEHOLDER_DEVICE_IP,
    PLACEHOLDER_DEVICE_NAME,
    STEP_BLE_SCAN,
    STEP_CLOUD_ACTION_SELECT,
    STEP_CLOUD_DEVICE_SELECT,
    STEP_DEVICE_SETUP,
    STEP_POP_INPUT,
    STEP_PROVISIONING,
    STEP_RAINMAKER_LOGIN,
    STEP_WIFI_CONFIG,
)
from .helpers.ha_types import ESPConfigEntryData

# Internal/advanced APIs - import from submodules as documented in iot/__init__.py
from .iot.client.security_validator import ESPSecurityManager
from .iot.cloud import BLEDeviceInfo, CloudDeviceInfo, RainMakerCLIClient
from .iot.discovery.network import ESPDeviceListener, async_discover_devices

# Public API - import from iot
from .iot.entity_states import DiscoveredDevice
from .iot.specs.device_specs import DEFAULT_DEVICE_NAME_PREFIX, DEFAULT_PORT
from .iot.specs.events import DOMAIN
from .iot.specs.keys import (
    CONF_CONNECTION_MODE,
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_RAINMAKER_REGION,
    CONF_SECURITY_VERSION,
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
    KEY_DEVICE_NAME,
    KEY_IP,
    KEY_POP,
    RAINMAKER_REGION_GLOBAL,
)

_LOGGER = logging.getLogger(__name__)


class ESPWeaverConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESP-Weaver integration.

    Supports automatic device discovery via mDNS/Zeroconf,
    security detection, PoP authentication, and cloud control.
    """

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._available_devices: list[DiscoveredDevice] = []
        self._selected_device: DiscoveredDevice | None = None
        self._security_manager = ESPSecurityManager()
        # Cloud-related state
        self._cli_client: RainMakerCLIClient | None = None
        self._cloud_devices: list[CloudDeviceInfo] = []
        self._rainmaker_region: Literal["global", "china"] = RAINMAKER_REGION_GLOBAL
        # BLE provisioning state
        self._selected_ble_device: BLEDeviceInfo | None = None
        self._wifi_ssid: str = ""
        self._wifi_password: str = ""
        self._device_pop: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow for this handler."""
        return ESPWeaverOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user.

        Shows connection mode selection: Local (mDNS) or Cloud (RainMaker).

        Args:
            user_input: User input containing connection mode.

        Returns:
            Flow result directing to local or cloud setup.
        """
        if user_input is not None:
            mode = user_input.get(CONF_CONNECTION_MODE, CONNECTION_MODE_LOCAL)
            if mode == CONNECTION_MODE_CLOUD:
                return await self.async_step_rainmaker_login()
            # Default to local mode
            return await self._async_step_local_discovery()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_MODE, default=CONNECTION_MODE_LOCAL
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    label="Local Control (mDNS Discovery)",
                                    value=CONNECTION_MODE_LOCAL,
                                ),
                                selector.SelectOptionDict(
                                    label="Cloud Control (ESP RainMaker)",
                                    value=CONNECTION_MODE_CLOUD,
                                ),
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_rainmaker_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle RainMaker login with email and password.

        Args:
            user_input: User input containing credentials and region.

        Returns:
            Flow result for action selection or error form.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._rainmaker_region = user_input.get(
                CONF_RAINMAKER_REGION, RAINMAKER_REGION_GLOBAL
            )
            email = user_input.get("email", "")
            password = user_input.get("password", "")

            # Create CLI client
            self._cli_client = RainMakerCLIClient(region=self._rainmaker_region)

            # Check if already logged in
            login_status = await self._cli_client.check_login_status()

            if not login_status.get("logged_in"):
                # Need to login with credentials
                if not email or not password:
                    errors["base"] = ERROR_RAINMAKER_NOT_LOGGED_IN
                else:
                    # Perform login via CLI
                    login_result = await self._cli_client.login(email, password)

                    if not login_result.get("success"):
                        _LOGGER.error(
                            "RainMaker login failed: %s",
                            login_result.get("error", "Unknown error"),
                        )
                        errors["base"] = ERROR_RAINMAKER_LOGIN_FAILED
                    else:
                        login_status = {"logged_in": True}

            if login_status.get("logged_in") and not errors:
                # Login successful, go to action selection
                return await self.async_step_cloud_action_select()

        return self.async_show_form(
            step_id=STEP_RAINMAKER_LOGIN,
            data_schema=vol.Schema(
                {
                    # China region requires OAuth (WeChat/Apple ID) which is
                    # not yet implemented.  For now only global is offered.
                    # Add China back when OAuth login is supported.
                    vol.Required(
                        CONF_RAINMAKER_REGION, default=RAINMAKER_REGION_GLOBAL
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    label="Global (api.rainmaker.espressif.com)",
                                    value=RAINMAKER_REGION_GLOBAL,
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required("email"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_cloud_action_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select action: add existing device or provision new device.

        Args:
            user_input: User input containing action selection.

        Returns:
            Flow result for next step.
        """
        if user_input is not None:
            action = user_input.get("action", "existing")

            if action == "provision":
                # Start BLE provisioning flow
                return await self.async_step_ble_scan()

            # Default: add existing device
            return await self._load_and_select_cloud_devices()

        return self.async_show_form(
            step_id=STEP_CLOUD_ACTION_SELECT,
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="existing"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    label="Add Existing Device (already in RainMaker)",
                                    value="existing",
                                ),
                                selector.SelectOptionDict(
                                    label="Provision New Device (via BLE)",
                                    value="provision",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def _load_and_select_cloud_devices(self) -> ConfigFlowResult:
        """Load cloud devices and go to selection."""
        if self._cli_client is None:
            return self.async_abort(reason=ERROR_RAINMAKER_NOT_LOGGED_IN)

        # Get cloud devices (retry up to 3 times for transient API failures)
        for attempt in range(3):
            try:
                self._cloud_devices = await self._cli_client.get_rainmaker_devices()
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Attempt %d: API call failed, retrying...", attempt + 1)
                self._cloud_devices = []
            if self._cloud_devices:
                break
            if attempt < 2:
                _LOGGER.debug(
                    "Attempt %d: no devices returned, retrying...", attempt + 1
                )
                await asyncio.sleep(2)

        if not self._cloud_devices:
            return self.async_abort(reason=ABORT_NO_DEVICES)

        # Filter out already configured devices
        existing_node_ids = self._get_existing_node_ids()
        self._cloud_devices = [
            d for d in self._cloud_devices if d.node_id not in existing_node_ids
        ]

        if not self._cloud_devices:
            return self.async_abort(reason=ABORT_NO_NEW_DEVICES)

        return await self.async_step_cloud_device_select()

    # =========================================================================
    # BLE Provisioning Flow (BLE 配网流程)
    # =========================================================================

    async def async_step_ble_scan(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Enter device name for provisioning.

        User enters the device name from serial log (e.g., PROV_724d86).

        Args:
            user_input: User input containing device name.

        Returns:
            Flow result for WiFi config or device name input form.
        """
        errors: dict[str, str] = {}

        if self._cli_client is None:
            return self.async_abort(reason=ERROR_RAINMAKER_NOT_LOGGED_IN)

        if user_input is not None:
            device_name = user_input.get("device_name", "").strip()

            if not device_name:
                errors["base"] = ERROR_INVALID_DEVICE
            elif not device_name.startswith("PROV_"):
                # Warn but allow non-standard names
                _LOGGER.warning("Device name doesn't start with PROV_: %s", device_name)

            if not errors:
                self._selected_ble_device = BLEDeviceInfo(
                    name=device_name,
                )
                _LOGGER.info("Using device name: %s", device_name)
                return await self.async_step_wifi_config()

        return self.async_show_form(
            step_id=STEP_BLE_SCAN,
            data_schema=vol.Schema(
                {
                    vol.Required("device_name"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_wifi_config(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure WiFi credentials for provisioning.

        Args:
            user_input: User input containing WiFi credentials.

        Returns:
            Flow result for provisioning or WiFi config form.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._wifi_ssid = user_input.get("wifi_ssid", "")
            self._wifi_password = user_input.get("wifi_password", "")
            self._device_pop = user_input.get("device_pop", "")

            if not self._wifi_ssid:
                errors["base"] = ERROR_WIFI_SSID_REQUIRED
            else:
                return await self.async_step_provisioning()

        return self.async_show_form(
            step_id=STEP_WIFI_CONFIG,
            data_schema=vol.Schema(
                {
                    vol.Required("wifi_ssid"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Optional("wifi_password", default=""): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional("device_pop", default=""): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                PLACEHOLDER_DEVICE_NAME: self._selected_ble_device.name
                if self._selected_ble_device
                else "Unknown",
            },
        )

    def _get_provisioning_error_schema(self) -> vol.Schema:
        """Return schema for provisioning error form with retry/back options."""
        return vol.Schema(
            {
                vol.Required("action", default="retry"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                label="Retry",
                                value="retry",
                            ),
                            selector.SelectOptionDict(
                                label="Back to WiFi config",
                                value="back",
                            ),
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )

    async def async_step_provisioning(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Execute provisioning and add device.

        Args:
            user_input: User input (confirmation or retry/back action).

        Returns:
            Flow result for entry creation or error.
        """
        errors: dict[str, str] = {}

        # Handle retry/back from error form
        if user_input is not None and "action" in user_input:
            if user_input.get("action") == "back":
                return await self.async_step_wifi_config()
            # action == "retry": fall through to retry provisioning

        if self._cli_client is None or self._selected_ble_device is None:
            return self.async_abort(reason=ERROR_INVALID_DEVICE)

        # Execute provisioning
        _LOGGER.info(
            "Provisioning device %s with WiFi %s...",
            self._selected_ble_device.name,
            self._wifi_ssid,
        )

        result = await self._cli_client.provision_device(
            device_name=self._selected_ble_device.name,
            ssid=self._wifi_ssid,
            wifi_password=self._wifi_password,
            pop=self._device_pop,
        )

        if not result.get("success"):
            _LOGGER.error("Provisioning failed: %s", result.get("error"))
            errors["base"] = ERROR_PROVISIONING_FAILED
            return self.async_show_form(
                step_id=STEP_PROVISIONING,
                data_schema=self._get_provisioning_error_schema(),
                errors=errors,
                description_placeholders={
                    "error": result.get("error", "Unknown error"),
                },
            )

        node_id = result.get("node_id")
        bound = result.get("bound", False)

        # Wait for device to appear in cloud account
        # Even if we have node_id, device may not be visible until claiming completes
        if not bound:
            _LOGGER.info(
                "Device %s provisioned but not yet bound to account, waiting...",
                node_id or "unknown",
            )

        # Wait and poll for device to appear in cloud
        # Device needs time for: WiFi connect + Self Claiming (30-60s for fresh
        # devices) + MQTT connect + User Node Association publish + Cloud processing
        existing_node_ids = self._get_existing_node_ids()
        max_retries = 24  # 24 x 5s = 120s max wait for Self Claiming to complete
        found_device = None

        for attempt in range(max_retries):
            await asyncio.sleep(5)
            try:
                self._cloud_devices = await self._cli_client.get_rainmaker_devices()
            except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    "Failed to fetch RainMaker devices (attempt %d): %s",
                    attempt + 1,
                    err,
                )
                self._cloud_devices = []
                continue

            if node_id:
                found_device = next(
                    (d for d in self._cloud_devices if d.node_id == node_id), None
                )
            else:
                new_devices = [
                    d for d in self._cloud_devices if d.node_id not in existing_node_ids
                ]
                if new_devices:
                    if len(new_devices) > 1:
                        _LOGGER.warning(
                            "Multiple new_devices (%d) but node_id not provided; "
                            "selecting first device. new_devices=%s",
                            len(new_devices),
                            [d.node_id for d in new_devices],
                        )
                    found_device = new_devices[0]

            if found_device:
                _LOGGER.info(
                    "Device %s found in cloud after %d seconds",
                    found_device.node_id,
                    (attempt + 1) * 5,
                )
                break

            # Log progress every 15 seconds
            elapsed = (attempt + 1) * 5
            if elapsed % 15 == 0:
                _LOGGER.info(
                    "Waiting for device Self Claiming... %d/%d seconds",
                    elapsed,
                    max_retries * 5,
                )
            else:
                _LOGGER.debug("Attempt %d: device not yet in cloud", attempt + 1)

        if found_device:
            return await self._create_cloud_entry(found_device)

        # Still no device found, show error with retry/back options
        errors["base"] = ERROR_PROVISIONING_FAILED
        return self.async_show_form(
            step_id=STEP_PROVISIONING,
            data_schema=self._get_provisioning_error_schema(),
            errors=errors,
            description_placeholders={
                "error": "Device provisioned but not found in cloud. Try again.",
            },
        )

    async def _async_step_local_discovery(self) -> ConfigFlowResult:
        """Handle local device discovery via mDNS/Zeroconf.

        Returns:
            Flow result directing to device setup or abort if no devices found.
        """
        self._security_manager.clear_cache()

        discovered_devices = await async_discover_devices(self.hass, ESPDeviceListener)
        if not discovered_devices:
            return self.async_abort(reason=ABORT_NO_DEVICES)

        existing_node_ids = self._get_existing_node_ids()

        # Process discovered devices and filter out already configured ones
        self._available_devices = [
            DiscoveredDevice(
                ip=device[KEY_IP],
                node_id=device[CONF_NODE_ID],
                port=device.get(CONF_PORT, DEFAULT_PORT),
                device_name=device.get(KEY_DEVICE_NAME),
            )
            for device in discovered_devices
            if device[CONF_NODE_ID] not in existing_node_ids
        ]

        if not self._available_devices:
            return self.async_abort(reason=ABORT_NO_NEW_DEVICES)

        # Detect security version for all devices in parallel
        await self._detect_security_versions()

        return await self.async_step_device_setup()

    async def async_step_cloud_device_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle cloud device selection.

        Args:
            user_input: User input containing selected device.

        Returns:
            Flow result for entry creation or form for device selection.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_node_id = user_input.get(FIELD_SELECTED_DEVICE)

            # Find selected device
            selected_device = next(
                (d for d in self._cloud_devices if d.node_id == selected_node_id),
                None,
            )

            if not selected_device:
                errors["base"] = ERROR_INVALID_DEVICE
            else:
                return await self._create_cloud_entry(selected_device)

        # Build device selection options
        options = [
            selector.SelectOptionDict(
                label=f"{device.name} ({device.node_id}) - "
                f"{'Online' if device.connected else 'Offline'}",
                value=device.node_id,
            )
            for device in self._cloud_devices
        ]

        return self.async_show_form(
            step_id=STEP_CLOUD_DEVICE_SELECT,
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_SELECTED_DEVICE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                PLACEHOLDER_DEVICE_COUNT: str(len(self._cloud_devices)),
            },
        )

    async def _create_cloud_entry(self, device: CloudDeviceInfo) -> ConfigFlowResult:
        """Create a config entry from cloud device.

        Args:
            device: CloudDeviceInfo for the selected device.

        Returns:
            Flow result with created config entry.
        """
        node_id = device.node_id

        await self.async_set_unique_id(node_id)
        self._abort_if_unique_id_configured()

        config_data: ESPConfigEntryData = {
            "node_id": node_id,
            "connection_mode": CONNECTION_MODE_CLOUD,
            "rainmaker_region": self._rainmaker_region,
        }

        return self.async_create_entry(
            title=f"{device.name} (Cloud)",
            data=config_data,
        )

    async def _detect_security_versions(self) -> None:
        """Detect security version for all discovered devices in parallel."""

        _PER_DEVICE_TIMEOUT = 5.0

        async def detect_for_device(device: DiscoveredDevice) -> None:
            """Detect security version for a single device."""
            try:
                security_info = await asyncio.wait_for(
                    self._security_manager.detect_device_security(
                        device.ip,
                        device.port,
                    ),
                    timeout=_PER_DEVICE_TIMEOUT,
                )
                device.security_version = security_info.get(CONF_SECURITY_VERSION, 0)
            except asyncio.CancelledError:
                device.security_version = 0
                raise
            except (OSError, TimeoutError, ConnectionError):
                _LOGGER.debug(
                    "Security detection failed for device %s (%s)",
                    device.node_id,
                    device.ip,
                )
                device.security_version = 0

        # Run all detections in parallel; each device has its own per-device
        # timeout so a stalled connection cannot hold up the entire gather.
        # The outer timeout acts as a safety net for unexpected hangs.
        tasks = [detect_for_device(device) for device in self._available_devices]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=10.0
            )
            # Log any unexpected exceptions from gather
            for result in results:
                if isinstance(result, Exception):
                    _LOGGER.error(
                        "Unexpected error during security detection: %s", result
                    )
        except TimeoutError:
            _LOGGER.warning("Security detection timed out for some devices")

    def _get_existing_node_ids(self) -> set[str]:
        """Get set of already configured node IDs."""
        return {
            entry.unique_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id
        }

    async def async_step_device_setup(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle device selection.

        Args:
            user_input: User input containing selected device.

        Returns:
            Flow result for next step or form for device selection.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_node_id = user_input.get(FIELD_SELECTED_DEVICE)

            selected_device = self._find_device_by_node_id(selected_node_id)
            if not selected_device:
                errors["base"] = ERROR_INVALID_DEVICE
            else:
                self._selected_device = selected_device

                # Use already detected security version (from _detect_security_versions)
                # security_version 1 requires PoP, security_version 0 does not
                if selected_device.security_version == 1:
                    return await self.async_step_pop_input()

                return await self._create_entry_from_device()

        return self.async_show_form(
            step_id=STEP_DEVICE_SETUP,
            data_schema=self._get_device_selection_schema(),
            errors=errors,
            description_placeholders={
                PLACEHOLDER_DEVICE_COUNT: str(len(self._available_devices)),
            },
        )

    def _find_device_by_node_id(self, node_id: str | None) -> DiscoveredDevice | None:
        """Find device by node ID.

        Args:
            node_id: The node ID to search for.

        Returns:
            DiscoveredDevice if found, None otherwise.
        """
        if not node_id:
            return None

        return next(
            (d for d in self._available_devices if d.node_id == node_id),
            None,
        )

    def _get_device_selection_schema(self) -> vol.Schema:
        """Return schema for device selection.

        Returns:
            Voluptuous schema for device selection form.
        """
        options = [
            selector.SelectOptionDict(
                label=device.display_name,
                value=device.node_id,
            )
            for device in self._available_devices
        ]

        return vol.Schema(
            {
                vol.Required(FIELD_SELECTED_DEVICE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def async_step_pop_input(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle PoP input step.

        Args:
            user_input: User input containing PoP value.

        Returns:
            Flow result for entry creation or form for PoP input.
        """
        errors: dict[str, str] = {}

        if self._selected_device is None:
            return self.async_abort(reason=ERROR_INVALID_DEVICE)

        if user_input is not None:
            pop_value = user_input.get(CONF_CUSTOM_POP, "")

            if not pop_value:
                errors["base"] = ERROR_POP_REQUIRED
            else:
                # Validate PoP - handle network errors gracefully
                try:
                    pop_valid = await self._security_manager.test_pop_connection(
                        self._selected_device.ip,
                        pop_value,
                        self._selected_device.port,
                    )
                except (OSError, TimeoutError, ConnectionError) as err:
                    _LOGGER.error(
                        "Connection failed during PoP validation for %s: %s",
                        self._selected_device.ip,
                        err,
                    )
                    errors["base"] = ERROR_CANNOT_CONNECT
                else:
                    if pop_valid:
                        self._selected_device.security_info[KEY_POP] = pop_value
                        return await self._create_entry_from_device()

                    errors["base"] = ERROR_INVALID_POP

        return self.async_show_form(
            step_id=STEP_POP_INPUT,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CUSTOM_POP): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                PLACEHOLDER_DEVICE_NAME: self._selected_device.get_simple_name(
                    DEFAULT_DEVICE_NAME_PREFIX
                ),
                PLACEHOLDER_DEVICE_IP: self._selected_device.ip,
            },
        )

    async def _create_entry_from_device(self) -> ConfigFlowResult:
        """Create a config entry from selected device.

        Returns:
            Flow result with created config entry.
        """
        device = self._selected_device
        if device is None:
            return self.async_abort(reason=ERROR_INVALID_DEVICE)

        node_id = device.node_id

        await self.async_set_unique_id(node_id)
        self._abort_if_unique_id_configured()

        # Use string literals for TypedDict keys (mypy requirement)
        config_data: ESPConfigEntryData = {
            "host": device.ip,
            "port": device.port,
            "node_id": node_id,
            "security_version": device.security_version or 0,
            "connection_mode": CONNECTION_MODE_LOCAL,
        }

        # Add PoP if provided
        pop = device.security_info.get(KEY_POP)
        if pop:
            config_data["custom_pop"] = pop

        return self.async_create_entry(
            title=device.get_simple_name(DEFAULT_DEVICE_NAME_PREFIX),
            data=config_data,
        )


class ESPWeaverOptionsFlow(OptionsFlow):
    """Handle ESP-Weaver options flow.

    Currently no user-configurable options are available.
    This provides a framework for future options.
    """

    async def async_step_init(
        self,
        _user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle options flow initialization.

        Args:
            user_input: User input data.

        Returns:
            Flow result - currently aborts as no options available.
        """
        # No user-configurable options currently available
        # Device settings are auto-discovered from the ESP device
        return self.async_abort(reason="no_options_available")
