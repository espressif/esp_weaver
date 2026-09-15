# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The options flow: change which product a device resolves against."""

from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from ..const import CONF_IDENTITY_ADVERTISED, CONF_NODE_ID
from ..data import domain_data
from ..profiles import ProfileBinding, async_keep, options_with_binding
from .const import (
    FIELD_ACTION,
    FIELD_PROFILE,
    FIELD_VENDOR,
    NAV_ACTION_BACK,
    STEP_INIT,
    STEP_PROFILE_SELECT,
)
from .profile_choice import ProfileChoiceMixin


class ESPWeaverOptionsFlow(ProfileChoiceMixin, OptionsFlow):
    """Change which product a device resolves against.

    Same list as the config flow; the node config is what the device has
    reported since it was added, and picking a product rebinds the entry.
    """

    def __init__(self) -> None:
        """Start with nothing chosen."""
        super().__init__()
        self._init_profile_choice()

    def _load_reported(self) -> None:
        """Take the node's config and values from what it has reported so far."""
        node_id = self.config_entry.data[CONF_NODE_ID]
        self._node_id = node_id
        # A cloud entry has no record of this and is judged in full.
        self._identity_advertised = self.config_entry.data.get(
            CONF_IDENTITY_ADVERTISED, True
        )
        api = domain_data(self.hass).api
        if api is None:
            return
        node_config, params = api.get_reported_config(node_id)
        self._node_config = node_config or {}
        self._node_params = params or {}

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer the vendors, naming the current product."""
        if not self._node_id:
            self._load_reported()

        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_choose_vendor(user_input[FIELD_VENDOR])
            if error is None:
                return await self.async_step_profile_select()
            errors["base"] = error

        return await self._async_show_vendor_select(
            STEP_INIT, errors=errors, current=self._current_id()
        )

    async def async_step_profile_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer the chosen vendor's products, naming the current one.

        The form offers to go back to the vendors.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[FIELD_ACTION] == NAV_ACTION_BACK:
                return await self.async_step_init()
            error = await self._async_choose_profile(user_input.get(FIELD_PROFILE))
            if error is None:
                return await self._async_summary()
            errors["base"] = error

        return await self._async_show_profile_select(
            STEP_PROFILE_SELECT, errors=errors, current=self._current_id()
        )

    def _current_id(self) -> str | None:
        """Return the id of the product the entry is bound to, if any."""
        current = ProfileBinding.from_entry(self.config_entry)
        return current.id if current else None

    async def _async_back_to_vendors(self) -> ConfigFlowResult:
        """Return to the vendor step."""
        return await self.async_step_init()

    async def _async_confirm(self) -> ConfigFlowResult:
        """Bind the entry to the chosen product, which reloads it."""
        # Chosen on the product step, which every way here goes through.
        assert self._chosen is not None
        binding = await async_keep(self.hass, self._chosen)
        # Finishing an options flow is what writes the options, and the entry's
        # update listener reloads it on seeing the binding change.
        return self.async_create_entry(
            data=options_with_binding(dict(self.config_entry.options), binding)
        )
