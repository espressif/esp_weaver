# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reaching a device on the local network: discovery, security, proof of possession."""

import asyncio
import logging
from typing import Any

from cryptography.exceptions import InvalidTag
from google.protobuf.message import DecodeError
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector
import voluptuous as vol

from ..const import CONF_CUSTOM_POP
from ..helpers.ha_types import ESPConfigEntryData
from ..helpers.host import HomeAssistantHost
from ..iot.client.node_reader import read_node
from ..iot.client.security_validator import ESPSecurityManager
from ..iot.discovered_device import DiscoveredDevice
from ..iot.discovery import async_discover_devices
from ..iot.specs.keys import CONNECTION_MODE_LOCAL
from ..profiles import ProductCandidate, misfit
from .binding import BindingSteps
from .const import (
    ABORT_NO_DEVICES,
    ABORT_NO_FITTING_DEVICES,
    ABORT_NO_NEW_DEVICES,
    ABORT_SECURITY_UNKNOWN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_DEVICE,
    ERROR_INVALID_POP,
    ERROR_POP_REQUIRED,
    FIELD_ACTION,
    FIELD_SELECTED_DEVICE,
    MESSAGE_DEVICES_MATCHED,
    MESSAGE_OTHER_DEVICES,
    NAV_ACTION_BACK,
    NOTHING,
    OTHER_DEVICE_LABEL,
    PLACEHOLDER_DEVICE_NAME,
    PLACEHOLDER_FOUND,
    PLACEHOLDER_MATCHED,
    PLACEHOLDER_PRODUCT,
    PLACEHOLDER_SILENT_NOTE,
    SECURITY_DETECT_TIMEOUT,
    SECURITY_DETECT_TOTAL_TIMEOUT,
    STEP_DEVICE_SETUP,
    STEP_POP_INPUT,
)
from .schemas import NAV_ACTION_FIELD, POP_INPUT_SCHEMA

_LOGGER = logging.getLogger(__name__)


def _advertised_as(product: ProductCandidate, device: DiscoveredDevice) -> bool:
    """Say whether the model in the device's mDNS record is the product's."""
    return device.model is not None and misfit(product, device.model) is None


def _advertised_nothing(device: DiscoveredDevice) -> bool:
    """Say whether the device's record carries no model: older firmware.

    Such a device cannot be told from the record; it is listed as Other
    device, and what it is made of is settled entity by entity once connected.
    """
    return device.model is None


class LocalDiscoverySteps(BindingSteps):
    """Find the device over mDNS, learn how it wants to be talked to, and read it."""

    def __init__(self) -> None:
        """Start with no devices found and none picked."""
        super().__init__()
        self._available_devices: list[DiscoveredDevice] = []
        self._selected_device: DiscoveredDevice | None = None
        self._security_manager = ESPSecurityManager()

    async def _async_step_local_discovery(self) -> ConfigFlowResult:
        """Look for devices on the local network, and ask which one to add."""
        self._security_manager.clear_cache()

        discovered_devices = await async_discover_devices(HomeAssistantHost(self.hass))
        if not discovered_devices:
            return self.async_abort(reason=ABORT_NO_DEVICES)

        existing_node_ids = self._get_existing_node_ids()
        new_devices = [
            device
            for device in discovered_devices
            if device.node_id not in existing_node_ids
        ]

        if not new_devices:
            return self.async_abort(reason=ABORT_NO_NEW_DEVICES)

        # Picked on the product step, which comes before any device is reached.
        chosen = self._chosen
        assert chosen is not None
        # The devices that advertised themselves as the product first, then
        # those that advertised nothing; a device that advertised itself as
        # something else is not listed.
        self._available_devices = [
            device for device in new_devices if _advertised_as(chosen, device)
        ] + [device for device in new_devices if _advertised_nothing(device)]
        if not self._available_devices:
            return self.async_abort(
                reason=ABORT_NO_FITTING_DEVICES,
                description_placeholders={
                    PLACEHOLDER_PRODUCT: self._product_title(chosen),
                    PLACEHOLDER_FOUND: str(len(new_devices)),
                },
            )

        # Detect security version for all devices in parallel
        await self._detect_security_versions()

        return await self.async_step_device_setup()

    async def _detect_security(self, device: DiscoveredDevice) -> None:
        """Ask one device how it expects to be talked to.

        Only the device's own answer is recorded; a device that did not answer
        stays ``None`` rather than being guessed either way.
        """
        try:
            device.security_version = await asyncio.wait_for(
                self._security_manager.ask_device_security(device.ip, device.port),
                timeout=SECURITY_DETECT_TIMEOUT,
            )
        except (TimeoutError, OSError):
            _LOGGER.debug(
                "Security detection failed for device %s (%s)",
                device.node_id,
                device.ip,
            )
            device.security_version = None

    async def _detect_security_versions(self) -> None:
        """Ask every discovered device at once; the unanswered are re-asked later."""
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(
                        self._detect_security(device)
                        for device in self._available_devices
                    )
                ),
                timeout=SECURITY_DETECT_TOTAL_TIMEOUT,
            )
        except TimeoutError:
            _LOGGER.warning("Security detection timed out for some devices")

    async def async_step_device_setup(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Pick one of the discovered devices to add.

        The form offers to go back to the products; choosing another scans
        the network again.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[FIELD_ACTION] == NAV_ACTION_BACK:
                return await self.async_step_profile_select()
            # The dropdown offers only what discovery listed, and the schema
            # has already refused anything else; only nothing can get here.
            node_id = user_input.get(FIELD_SELECTED_DEVICE)
            device = next(
                (d for d in self._available_devices if d.node_id == node_id), None
            )
            if device is None:
                errors["base"] = ERROR_INVALID_DEVICE
                return await self._async_show_device_setup(errors)
            self._selected_device = device

            if device.security_version is None:
                # Did not answer during the scan (HTTP server still starting);
                # ask again.
                await self._detect_security(device)

            # Still no answer: there is no telling whether to ask for a PoP,
            # so the flow ends and says to try again once the device is up.
            if device.security_version is None:
                return self.async_abort(
                    reason=ABORT_SECURITY_UNKNOWN,
                    description_placeholders=self._device_placeholders(device),
                )

            # sec0 alone needs no PoP. sec1 (AES-CTR) and sec2 (SRP6a) do.
            if device.security_version == 0:
                return await self._create_entry_from_device()

            return await self.async_step_pop_input()

        return await self._async_show_device_setup(errors)

    async def _async_show_device_setup(
        self, errors: dict[str, str]
    ) -> ConfigFlowResult:
        """Show the list of devices found, and the way back to the products."""
        chosen = self._chosen
        silent = sum(1 for d in self._available_devices if _advertised_nothing(d))
        matched = await self._async_message(
            MESSAGE_DEVICES_MATCHED,
            count=str(len(self._available_devices) - silent),
            product=self._product_title(chosen) if chosen else NOTHING,
        )
        # The Other device line is only worth saying while such a device is
        # on the list; a count of none would leave a dangling "0 devices".
        silent_note = (
            f"\n\n{await self._async_message(MESSAGE_OTHER_DEVICES, silent=str(silent))}"
            if silent
            else ""
        )
        return self.async_show_form(
            step_id=STEP_DEVICE_SETUP,
            data_schema=self._get_device_selection_schema(),
            errors=errors,
            description_placeholders={
                PLACEHOLDER_MATCHED: matched,
                PLACEHOLDER_SILENT_NOTE: silent_note,
            },
        )

    def _get_device_selection_schema(self) -> vol.Schema:
        """Return the dropdown of everything discovery turned up."""
        options = [
            selector.SelectOptionDict(
                label=self._device_label(device), value=device.node_id
            )
            for device in self._available_devices
        ]
        return vol.Schema(
            {
                vol.Optional(FIELD_SELECTED_DEVICE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                **NAV_ACTION_FIELD,
            }
        )

    def _device_label(self, device: DiscoveredDevice) -> str:
        """Return how the list names a device: ``Light-A10-1``.

        The product's label, the model the device advertised, and a number.
        Neither address nor node id: a user holding the bulb knows neither,
        and the number tells the devices apart. A device that advertised
        nothing is ``Other device-1``: what it is is not known until it is
        read, and the node id would say nothing to the user either. Each kind
        is numbered on its own, in the order the list shows.
        """
        silent = _advertised_nothing(device)
        kin = [d for d in self._available_devices if _advertised_nothing(d) is silent]
        position = kin.index(device) + 1
        if silent:
            return f"{OTHER_DEVICE_LABEL}-{position}"
        chosen = self._chosen
        name = (
            "-".join(part for part in (chosen.label, device.model) if part)
            if chosen
            else OTHER_DEVICE_LABEL
        )
        return f"{name}-{position}"

    async def async_step_pop_input(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask for the PoP a sec1 or sec2 device needs, and try it on the device.

        The form offers to go back to the device list.
        """
        # Picked on the device step, the only way here.
        device = self._selected_device
        assert device is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input[FIELD_ACTION] == NAV_ACTION_BACK:
                return await self.async_step_device_setup()
            pop_value = user_input[CONF_CUSTOM_POP]

            if not pop_value:
                errors["base"] = ERROR_POP_REQUIRED
            elif (error := await self._test_pop(device, pop_value)) is None:
                device.pop = pop_value
                return await self._create_entry_from_device()
            else:
                errors["base"] = error

        return self.async_show_form(
            step_id=STEP_POP_INPUT,
            data_schema=POP_INPUT_SCHEMA,
            errors=errors,
            description_placeholders=self._device_placeholders(device),
        )

    def _device_placeholders(self, device: DiscoveredDevice) -> dict[str, str]:
        """Return the name a step's text calls the device by: its line in the list.

        ``Light-A10-2``, the one thing about the device the user has seen; the
        node id and address mean nothing to someone holding the bulb.
        """
        return {PLACEHOLDER_DEVICE_NAME: self._device_label(device)}

    async def _test_pop(self, device: DiscoveredDevice, pop_value: str) -> str | None:
        """Try the code against the device; return the error to show, if any."""
        # Anything the handshake can fail with is "could not connect" here.
        try:
            pop_valid = await self._security_manager.test_pop_connection(
                device.ip, pop_value, device.port
            )
        except (OSError, RuntimeError, ValueError, DecodeError, InvalidTag) as err:
            _LOGGER.error(
                "Connection failed during PoP validation for %s: %s", device.ip, err
            )
            return ERROR_CANNOT_CONNECT
        return None if pop_valid else ERROR_INVALID_POP

    async def _create_entry_from_device(self) -> ConfigFlowResult:
        """Create the config entry for a device reached on the local network."""
        device = self._selected_device
        assert device is not None
        node_id = device.node_id

        await self.async_set_unique_id(node_id)
        self._abort_if_unique_id_configured()

        # Settled by the time an entry is made: either detection answered sec0,
        # or the PoP step's handshake succeeded and recorded what it used.
        assert device.security_version is not None
        # Spelled out rather than built from the CONF_* constants, because a
        # TypedDict can only be indexed by a literal key.
        # A device that advertised nothing has its model held against no
        # product, now or on a rebind: its firmware reports its project name.
        self._identity_advertised = not _advertised_nothing(device)
        config_data: ESPConfigEntryData = {
            "host": device.ip,
            "port": device.port,
            "node_id": node_id,
            "security_version": device.security_version,
            "connection_mode": CONNECTION_MODE_LOCAL,
            "identity_advertised": self._identity_advertised,
        }

        if device.pop:
            config_data["custom_pop"] = device.pop

        # The name advertised over mDNS, if any, for when the node gives none;
        # the title is settled when the entry is created, after the node is read.
        self._pending_entry = (device.device_name or "", config_data)
        await self._async_read_local_node(device)
        return await self._async_summary()

    async def _async_read_local_node(self, device: DiscoveredDevice) -> None:
        """Read the node config for binding; empty if the device does not answer."""
        self._node_id = device.node_id
        if (reported := await read_node(device)) is None:
            _LOGGER.debug(
                "No setup summary for %s, which did not answer", device.node_id
            )
            return
        self._node_config, self._node_params = reported
