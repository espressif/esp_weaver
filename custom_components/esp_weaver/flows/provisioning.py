# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Giving a new device its Wi-Fi through RainMaker, then adding it as a cloud node."""

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlowResult

from ..iot.cloud import CloudDeviceInfo, RainMakerCLIClient
from .cloud import CloudSteps
from .const import (
    ERROR_INVALID_DEVICE,
    ERROR_PROVISIONING_AMBIGUOUS,
    ERROR_PROVISIONING_FAILED,
    ERROR_PROVISIONING_NOT_IN_CLOUD,
    ERROR_WIFI_PASSWORD_REQUIRED,
    ERROR_WIFI_SSID_REQUIRED,
    FIELD_ACTION,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_POP,
    FIELD_WIFI_PASSWORD,
    FIELD_WIFI_SSID,
    PLACEHOLDER_DEVICE_NAME,
    PLACEHOLDER_ERROR,
    PROGRESS_PROVISIONING,
    PROVISION_POLL_ATTEMPTS,
    PROVISION_POLL_INTERVAL,
    PROVISIONING_ACTION_BACK,
    PROVISIONING_NAME_PREFIX,
    STEP_BLE_SCAN,
    STEP_PROVISIONING,
    STEP_PROVISIONING_FAILED,
    STEP_PROVISIONING_RESULT,
    STEP_WIFI_CONFIG,
)
from .schemas import BLE_SCAN_SCHEMA, PROVISIONING_ERROR_SCHEMA, WIFI_CONFIG_SCHEMA

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProvisioningOutcome:
    """What one run of provisioning ended with.

    Either ``error`` is set, or ``found`` holds every node that could be the
    new one: one is the answer, none or several is not.
    """

    error: str | None = None
    found: list[CloudDeviceInfo] = field(default_factory=list)


class ProvisioningSteps(CloudSteps):
    """Name the device, give it a network, and wait for the account to list it."""

    def __init__(self) -> None:
        """Start with no device named and no network given."""
        super().__init__()
        self._ble_device_name: str | None = None
        self._wifi_ssid: str = ""
        self._wifi_password: str = ""
        self._device_pop: str = ""
        # The run in progress, and what it ended with once it has.
        self._provision_task: asyncio.Task[ProvisioningOutcome] | None = None
        self._outcome: ProvisioningOutcome | None = None
        # The failure last shown, so the form can be shown again as it was.
        self._last_error: str = ""
        self._last_error_key: str = ERROR_PROVISIONING_FAILED

    async def async_step_ble_scan(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask which device to provision, by its BLE name (normally ``PROV_xxxxxx``)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_name = user_input[FIELD_DEVICE_NAME].strip()

            if not device_name:
                errors["base"] = ERROR_INVALID_DEVICE
            else:
                if not device_name.startswith(PROVISIONING_NAME_PREFIX):
                    _LOGGER.warning(
                        "Device name %s does not start with %s",
                        device_name,
                        PROVISIONING_NAME_PREFIX,
                    )
                self._ble_device_name = device_name
                return await self.async_step_wifi_config()

        return self.async_show_form(
            step_id=STEP_BLE_SCAN,
            data_schema=BLE_SCAN_SCHEMA,
            errors=errors,
        )

    async def async_step_wifi_config(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask for the WiFi the device is to join, and its PoP.

        The password is required: without one the CLI drops into an interactive
        picker, so an open network cannot be provisioned through it.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._wifi_ssid = user_input[FIELD_WIFI_SSID]
            self._wifi_password = user_input[FIELD_WIFI_PASSWORD]
            self._device_pop = user_input[FIELD_DEVICE_POP]

            if not self._wifi_ssid:
                errors["base"] = ERROR_WIFI_SSID_REQUIRED
            elif not self._wifi_password:
                errors["base"] = ERROR_WIFI_PASSWORD_REQUIRED
            else:
                return await self.async_step_provisioning()

        return self.async_show_form(
            step_id=STEP_WIFI_CONFIG,
            data_schema=WIFI_CONFIG_SCHEMA,
            errors=errors,
            description_placeholders={
                PLACEHOLDER_DEVICE_NAME: self._ble_device_name or "Unknown",
            },
        )

    async def async_step_provisioning(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Provision the device and wait for the account to list it, as a task.

        The whole run takes minutes, longer than a form can be held open, so
        the dialog shows progress and Home Assistant calls this step again when
        the task finishes.
        """
        if self._provision_task is None:
            # Set by the login and device steps, which every way here goes through.
            assert self._cli_client is not None
            assert self._ble_device_name is not None
            # Not started eagerly: the dialog is to show progress first, and
            # the task is to end by having Home Assistant call this step again.
            self._provision_task = self.hass.async_create_task(
                self._async_provision(self._cli_client, self._ble_device_name),
                name="esp_weaver_provisioning",
                eager_start=False,
            )

        if not self._provision_task.done():
            return self.async_show_progress(
                step_id=STEP_PROVISIONING,
                progress_action=PROGRESS_PROVISIONING,
                progress_task=self._provision_task,
                description_placeholders={
                    PLACEHOLDER_DEVICE_NAME: self._ble_device_name or "Unknown",
                },
            )

        try:
            self._outcome = self._provision_task.result()
        except Exception as err:  # pylint: disable=broad-exception-caught
            # A bug worth a traceback, but the dialog still needs a way on.
            _LOGGER.exception(
                "Provisioning %s failed unexpectedly", self._ble_device_name
            )
            self._outcome = ProvisioningOutcome(error=str(err))
        finally:
            self._provision_task = None

        return self.async_show_progress_done(next_step_id=STEP_PROVISIONING_RESULT)

    async def async_step_provisioning_result(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Act on what the run ended with: add the node, or say what went wrong."""
        outcome = self._outcome
        self._outcome = None
        # Reached only through the progress step, which always leaves one.
        assert outcome is not None

        if outcome.error is not None:
            return self._show_provisioning_error(outcome.error)

        found = outcome.found
        if len(found) == 1:
            return await self._create_cloud_entry(found[0])
        if found:
            # Several nodes joined the account meanwhile and the tool did not
            # say which is ours; do not guess.
            return self._show_provisioning_error(
                ", ".join(device.node_id for device in found),
                ERROR_PROVISIONING_AMBIGUOUS,
            )
        return self._show_provisioning_error("", ERROR_PROVISIONING_NOT_IN_CLOUD)

    async def async_step_provisioning_failed(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Retry with the same details, or go back to correct them."""
        if user_input is None:
            # The frontend rereads the form it is showing; it is shown as it was.
            return self._show_provisioning_error(self._last_error, self._last_error_key)
        if user_input[FIELD_ACTION] == PROVISIONING_ACTION_BACK:
            return await self.async_step_wifi_config()
        return await self.async_step_provisioning()

    def _show_provisioning_error(
        self, detail: str, error: str = ERROR_PROVISIONING_FAILED
    ) -> ConfigFlowResult:
        """Report a provisioning that did not finish, offering retry or back."""
        self._last_error, self._last_error_key = detail, error
        return self.async_show_form(
            step_id=STEP_PROVISIONING_FAILED,
            data_schema=PROVISIONING_ERROR_SCHEMA,
            errors={"base": error},
            description_placeholders={PLACEHOLDER_ERROR: detail},
        )

    async def _async_provision(
        self, client: RainMakerCLIClient, device_name: str
    ) -> ProvisioningOutcome:
        """Run the whole of provisioning, off the dialog.

        The account is read first, so the node the run adds can be told from
        what was already there when the tool reports no node id.
        """
        before = await self._async_account_snapshot(client)

        _LOGGER.info(
            "Provisioning device %s with WiFi %s...", device_name, self._wifi_ssid
        )
        result = await client.provision_device(
            device_name=device_name,
            ssid=self._wifi_ssid,
            wifi_password=self._wifi_password,
            pop=self._device_pop,
        )

        if not result.get("success"):
            _LOGGER.error("Provisioning failed: %s", result.get("error"))
            return ProvisioningOutcome(error=result.get("error", ""))

        found = await self._async_wait_for_provisioned_device(
            client, result.get("node_id"), before
        )
        if len(found) == 1:
            # Read here, while the dialog is still showing progress, rather
            # than in the step that creates the entry.
            await self._wait_for_node_config(found[0].node_id)
        return ProvisioningOutcome(found=found)

    async def _async_account_snapshot(self, client: RainMakerCLIClient) -> set[str]:
        """Return every node the account lists now, plus every node added here.

        Falls back to the configured entries when the account cannot be read.
        """
        known = self._get_existing_node_ids()
        try:
            listed = await client.get_rainmaker_devices()
        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Account not readable before provisioning: %s", err)
            return known
        return known | {device.node_id for device in listed}

    async def _async_wait_for_provisioned_device(
        self, client: RainMakerCLIClient, node_id: str | None, before: set[str]
    ) -> list[CloudDeviceInfo]:
        """Poll the account until the node the flow just provisioned appears.

        The tool answers as soon as the device has its credentials, well before
        the cloud lists it.

        Args:
            node_id: The node the tool reported, if any. Without it the device
                is the one that was not in the account before.
            before: Every node the account listed before the tool ran.

        Returns:
            The candidates: empty if none turned up, several if the account
            gained several meanwhile.
        """
        for attempt in range(PROVISION_POLL_ATTEMPTS):
            await asyncio.sleep(PROVISION_POLL_INTERVAL)
            try:
                self._cloud_devices = await client.get_rainmaker_devices()
            except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    "Failed to fetch RainMaker devices (attempt %d): %s",
                    attempt + 1,
                    err,
                )
                self._cloud_devices = []
                continue

            found = self._match_provisioned_device(node_id, before)
            if found:
                _LOGGER.info(
                    "Device %s found in cloud after %d seconds",
                    ", ".join(device.node_id for device in found),
                    (attempt + 1) * PROVISION_POLL_INTERVAL,
                )
                return found

            _LOGGER.debug("Attempt %d: device not yet in cloud", attempt + 1)

        return []

    def _match_provisioned_device(
        self, node_id: str | None, before: set[str]
    ) -> list[CloudDeviceInfo]:
        """Pick the newly provisioned node out of what the account now lists."""
        if node_id:
            return [d for d in self._cloud_devices if d.node_id == node_id]
        return [d for d in self._cloud_devices if d.node_id not in before]
