# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reaching a device through its RainMaker account."""

import asyncio
import logging
from typing import Any, Literal

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import selector
import voluptuous as vol

from ..const import (
    CLOUD_CONFIG_WAIT_ATTEMPTS,
    CLOUD_CONFIG_WAIT_INTERVAL,
    CONF_RAINMAKER_REGION,
)
from ..helpers.ha_types import ESPConfigEntryData
from ..iot.cloud import CloudDeviceInfo, RainMakerCLIClient
from ..iot.specs.keys import CONNECTION_MODE_CLOUD, RAINMAKER_REGION_GLOBAL
from .binding import BindingSteps
from .const import (
    ABORT_NO_DEVICES,
    ABORT_NO_NEW_DEVICES,
    CLOUD_ACTION_PROVISION,
    CLOUD_DEVICE_FETCH_ATTEMPTS,
    CLOUD_DEVICE_FETCH_INTERVAL,
    ERROR_RAINMAKER_LOGIN_FAILED,
    ERROR_RAINMAKER_NOT_LOGGED_IN,
    FIELD_ACTION,
    FIELD_SELECTED_DEVICE,
    MESSAGE_DEVICE_OFFLINE,
    MESSAGE_DEVICE_ONLINE,
    PLACEHOLDER_DEVICE_COUNT,
    STEP_CLOUD_ACTION_SELECT,
    STEP_CLOUD_DEVICE_SELECT,
    STEP_RAINMAKER_LOGIN,
)
from .schemas import CLOUD_ACTION_SCHEMA, RAINMAKER_LOGIN_SCHEMA

_LOGGER = logging.getLogger(__name__)


class CloudSteps(BindingSteps):
    """Sign in to RainMaker and pick one of the account's devices."""

    def __init__(self) -> None:
        """Start signed out, with no account read."""
        super().__init__()
        self._cli_client: RainMakerCLIClient | None = None
        self._cloud_devices: list[CloudDeviceInfo] = []
        self._rainmaker_region: Literal["global", "china"] = RAINMAKER_REGION_GLOBAL

    async def async_step_ble_scan(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Start provisioning a new device; the provisioning steps take it from here.

        Raises:
            NotImplementedError: Unless the provisioning steps are part of the flow.
        """
        raise NotImplementedError

    async def async_step_rainmaker_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Sign in to a RainMaker account."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_login(user_input)
            if error is None:
                return await self.async_step_cloud_action_select()
            errors["base"] = error

        return self.async_show_form(
            step_id=STEP_RAINMAKER_LOGIN,
            data_schema=RAINMAKER_LOGIN_SCHEMA,
            errors=errors,
        )

    async def _async_login(self, user_input: dict[str, Any]) -> str | None:
        """Sign in to RainMaker, or say which error to show.

        The CLI's own session is reused when it belongs to the typed account
        (or the form is empty); a session for another account is ended first.

        Returns:
            An error key for the form, or None once there is a session.
        """
        self._rainmaker_region = user_input[CONF_RAINMAKER_REGION]
        self._cli_client = RainMakerCLIClient(region=self._rainmaker_region)

        email = user_input[CONF_EMAIL].strip()
        password = user_input[CONF_PASSWORD]

        login_status = await self._cli_client.check_login_status()
        if login_status.get("logged_in"):
            current = login_status.get("email")
            if not email or current is None or current == email.lower():
                return None
            # The session is another account's; sign in as the one typed.
            _LOGGER.info("RainMaker session belongs to another account; signing out")
            await self._cli_client.logout()

        if not email or not password:
            return ERROR_RAINMAKER_NOT_LOGGED_IN

        login_result = await self._cli_client.login(email, password)
        if not login_result.get("success"):
            _LOGGER.error(
                "RainMaker login failed: %s",
                login_result.get("error", "Unknown error"),
            )
            return ERROR_RAINMAKER_LOGIN_FAILED

        return None

    async def async_step_cloud_action_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose between adding a device the account has and provisioning a new one."""
        if user_input is not None:
            action = user_input[FIELD_ACTION]

            if action == CLOUD_ACTION_PROVISION:
                # Start BLE provisioning flow
                return await self.async_step_ble_scan()

            # Default: add existing device
            return await self._load_and_select_cloud_devices()

        return self.async_show_form(
            step_id=STEP_CLOUD_ACTION_SELECT,
            data_schema=CLOUD_ACTION_SCHEMA,
        )

    async def _load_and_select_cloud_devices(self) -> ConfigFlowResult:
        """Read the account, then offer whatever is not configured already."""
        # Reached only from the action step, which the login step sets this for.
        assert self._cli_client is not None
        await self._async_fetch_cloud_devices(self._cli_client)
        if not self._cloud_devices:
            return self.async_abort(reason=ABORT_NO_DEVICES)

        existing_node_ids = self._get_existing_node_ids()
        self._cloud_devices = [
            device
            for device in self._cloud_devices
            if device.node_id not in existing_node_ids
        ]
        if not self._cloud_devices:
            return self.async_abort(reason=ABORT_NO_NEW_DEVICES)

        return await self.async_step_cloud_device_select()

    async def _async_fetch_cloud_devices(self, client: RainMakerCLIClient) -> None:
        """Read the account's device list, giving the API a few chances."""
        for attempt in range(CLOUD_DEVICE_FETCH_ATTEMPTS):
            try:
                self._cloud_devices = await client.get_rainmaker_devices()
            except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Reading the RainMaker account failed: %s", err)
                self._cloud_devices = []

            if self._cloud_devices:
                return

            if attempt + 1 < CLOUD_DEVICE_FETCH_ATTEMPTS:
                _LOGGER.debug("Attempt %d returned no devices", attempt + 1)
                await asyncio.sleep(CLOUD_DEVICE_FETCH_INTERVAL)

    async def async_step_cloud_device_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Pick one of the account's devices to add."""
        if user_input is not None:
            # The dropdown offers only what the account listed, and the schema
            # has already refused anything else.
            node_id = user_input[FIELD_SELECTED_DEVICE]
            device = next(d for d in self._cloud_devices if d.node_id == node_id)
            return await self._create_cloud_entry(device)

        online = await self._async_message(MESSAGE_DEVICE_ONLINE)
        offline = await self._async_message(MESSAGE_DEVICE_OFFLINE)
        options = [
            selector.SelectOptionDict(
                label=f"{device.name} ({device.node_id}) - "
                f"{online if device.connected else offline}",
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
            description_placeholders={
                PLACEHOLDER_DEVICE_COUNT: str(len(self._cloud_devices)),
            },
        )

    async def _create_cloud_entry(self, device: CloudDeviceInfo) -> ConfigFlowResult:
        """Create the config entry for a device reached through RainMaker."""
        node_id = device.node_id

        await self.async_set_unique_id(node_id)
        self._abort_if_unique_id_configured()

        await self._wait_for_node_config(node_id)

        config_data: ESPConfigEntryData = {
            "node_id": node_id,
            "connection_mode": CONNECTION_MODE_CLOUD,
            "rainmaker_region": self._rainmaker_region,
        }

        # The account's name for the device, for when the node gives none; the
        # title is settled when the entry is created.
        self._pending_entry = (device.name or "", config_data)
        # No parameter values: the skip reasons are decided by the names in the
        # object model, which the wait above already fetched.
        self._node_id = node_id
        self._node_params = {}
        return await self._async_summary()

    async def _wait_for_node_config(self, node_id: str) -> None:
        """Wait until the cloud can say what the node is made of.

        A node is listed as soon as it is bound, seconds before its
        configuration is stored. If the wait runs out, ``_node_config`` stays
        empty and the flow ends saying so.
        """
        # Signed in before any device could be listed to pick from.
        assert self._cli_client is not None
        if self._node_config and self._node_id == node_id:
            # Already read for this node - by the provisioning task, while the
            # dialog was showing progress. Not worth another round trip.
            return
        for attempt in range(CLOUD_CONFIG_WAIT_ATTEMPTS):
            try:
                config = await self._cli_client.get_node_details(node_id)
            except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Node config for %s not readable: %s", node_id, err)
                config = None

            if config and "devices" in config:
                if attempt:
                    _LOGGER.info(
                        "Config for %s available after %d seconds",
                        node_id,
                        attempt * CLOUD_CONFIG_WAIT_INTERVAL,
                    )
                # Kept so the product can be rehearsed against it rather than
                # fetch the same thing again.
                self._node_config = config
                self._node_id = node_id
                return

            if attempt + 1 < CLOUD_CONFIG_WAIT_ATTEMPTS:
                await asyncio.sleep(CLOUD_CONFIG_WAIT_INTERVAL)

        _LOGGER.warning(
            "Cloud has no config for %s after %d seconds; it cannot be added "
            "until it has one",
            node_id,
            CLOUD_CONFIG_WAIT_ATTEMPTS * CLOUD_CONFIG_WAIT_INTERVAL,
        )
