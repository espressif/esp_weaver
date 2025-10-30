# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Config flow for ESP-Weaver integration.

This module handles the configuration flow for adding ESP devices to Home Assistant,
including device discovery, security detection, PoP authentication, and options flow.
"""

import asyncio
import logging
from typing import Any

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
    FIELD_SELECTED_DEVICE,
    PLACEHOLDER_DEVICE_COUNT,
    PLACEHOLDER_DEVICE_IP,
    PLACEHOLDER_DEVICE_NAME,
    STEP_DEVICE_SETUP,
    STEP_POP_INPUT,
)
from .helpers.ha_types import ESPConfigEntryData

# Internal/advanced APIs - import from submodules as documented in iot/__init__.py
from .iot.client.security_validator import ESPSecurityManager
from .iot.discovery.network import ESPDeviceListener, async_discover_devices

# Public API - import from iot
from .iot.entity_states import DiscoveredDevice
from .iot.specs.device_specs import DEFAULT_DEVICE_NAME_PREFIX, DEFAULT_PORT
from .iot.specs.events import DOMAIN
from .iot.specs.keys import (
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_SECURITY_VERSION,
    KEY_DEVICE_NAME,
    KEY_IP,
    KEY_POP,
)

_LOGGER = logging.getLogger(__name__)


class ESPWeaverConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESP-Weaver integration.

    Supports automatic device discovery via mDNS/Zeroconf,
    security detection, and PoP authentication for secure devices.
    """

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._available_devices: list[DiscoveredDevice] = []
        self._selected_device: DiscoveredDevice | None = None
        self._security_manager = ESPSecurityManager()

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

        Automatically discovers ESP devices on the network via mDNS/Zeroconf.

        Args:
            user_input: Required by ConfigFlow signature, not used here.

        Returns:
            Flow result directing to device setup or abort if no devices found.
        """
        # user_input is not used in this step (discovery is automatic)

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
