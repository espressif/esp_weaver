# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Where the local, cloud and provisioning steps converge: bind and create."""

from abc import abstractmethod
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from ..const import CONF_NODE_ID
from ..helpers.ha_types import ESPConfigEntryData
from ..identity import DOMAIN
from ..iot.specs.keys import (
    CONNECTION_MODE_CLOUD,
    KEY_DEVICES,
    KEY_INFO,
    KEY_NAME,
    KEY_PARAMS,
    KEY_TYPE,
    PARAM_TYPE_NAME,
)
from ..profiles import async_keep, options_with_binding
from .const import CLOUD_TITLE_SUFFIX, OTHER_DEVICE_LABEL
from .profile_choice import ProfileChoiceMixin


class BindingSteps(ProfileChoiceMixin, ConfigFlow):
    """The steps from a device in hand to an entry bound to a product.

    A base the step groups build on, not a flow of its own.
    """

    def __init__(self) -> None:
        """Start with nothing chosen and no entry to create."""
        super().__init__()
        self._init_profile_choice()
        # What binding creates; set by the local, cloud and provisioning
        # steps once the device has been read.
        self._pending_entry: tuple[str, ESPConfigEntryData] | None = None

    def _get_existing_node_ids(self) -> set[str]:
        """Get set of already configured node IDs."""
        return {
            entry.data[CONF_NODE_ID]
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }

    async def _async_summary(self) -> ConfigFlowResult:
        """Rehearse the product against the device in hand, then create the entry."""
        # Set by the steps that lead here; the flow offers no way around them.
        assert self._pending_entry is not None
        return await super()._async_summary()

    async def _async_back_to_vendors(self) -> ConfigFlowResult:
        """Return to the vendor step, keeping the device that was reached."""
        return await self.async_step_profile_vendor()

    @abstractmethod
    async def async_step_profile_vendor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The vendor step, provided by the flow class."""

    @abstractmethod
    async def async_step_profile_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The product step, provided by the flow class; the device list goes back to it."""

    async def _async_confirm(self) -> ConfigFlowResult:
        """Create the entry, bound to the product that was chosen for it."""
        assert self._pending_entry is not None
        assert self._chosen is not None
        binding = await async_keep(self.hass, self._chosen)

        advertised, config_data = self._pending_entry
        return self.async_create_entry(
            title=self._pending_title(advertised, config_data),
            data=config_data,
            options=options_with_binding(None, binding),
        )

    def _device_names(self) -> list[str]:
        """Return what the node calls each of its devices, ``["Light"]``.

        The value of the device's ``Name`` parameter, which is what the user
        sees and may have changed; the name the node config declares it under
        when there is no such parameter or no value for it.
        """
        names: list[str] = []
        for entry in self._node_config.get(KEY_DEVICES) or []:
            if not isinstance(entry, dict) or not (name := entry.get(KEY_NAME)):
                continue
            values = self._node_params.get(name) or {}
            for param in entry.get(KEY_PARAMS) or []:
                if isinstance(param, dict) and param.get(KEY_TYPE) == PARAM_TYPE_NAME:
                    current = values.get(param.get(KEY_NAME))
                    if isinstance(current, str) and current.strip():
                        name = current.strip()
                    break
            names.append(str(name))
        return names

    def _node_name(self) -> str | None:
        """Return the name the node gives the device, or None if it gives none.

        A node with one device is called what that device is called,
        ``Light``; one with several goes by the node's own name, or the first
        device's failing that.
        """
        names = self._device_names()
        if len(names) == 1:
            return names[0]
        node_name = (self._node_config.get(KEY_INFO) or {}).get(KEY_NAME)
        if node_name:
            return str(node_name)
        return names[0] if names else None

    def _pending_title(self, advertised: str, config_data: ESPConfigEntryData) -> str:
        """Return the entry's title, worked out once the node has been read.

        The name the node gives; failing that, the one the device advertised
        before it was reached; failing both, ``Other device``: not a
        vendor's name, since the products are many vendors', and not the node
        id, which means nothing to the user. Renamed on the device page.
        """
        title = self._node_name() or advertised or OTHER_DEVICE_LABEL
        if config_data.get("connection_mode") == CONNECTION_MODE_CLOUD:
            title = f"{title}{CLOUD_TITLE_SUFFIX}"
        return title
