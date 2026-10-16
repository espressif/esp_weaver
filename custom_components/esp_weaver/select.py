# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver select entity, driven entirely by device profiles."""

import logging
from typing import Any, Final

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform

from .entity import ESPWeaverBindingEntity
from .helpers.discovery import binding_platform_setup

_LOGGER = logging.getLogger(__name__)

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverSelect(ESPWeaverBindingEntity, SelectEntity):
    """A select whose options come from a device profile, or the firmware where it reported them."""

    def _configure(self) -> None:
        """Set up the select."""
        binding = self._binding
        self._attr_options = list(binding.options or ())
        self._attr_current_option = None

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the option the device reported, if it is one we offer; Home Assistant rejects others."""
        option = self._read_primary(reported)
        if option is None:
            return False

        option = str(option)
        if self._attr_options and option not in self._attr_options:
            _LOGGER.warning(
                "Device %s reported %r for %s, which is not one of %s",
                self._node_id,
                option,
                self._binding.name,
                self._attr_options,
            )
            return False

        if option == self._attr_current_option:
            return False

        self._attr_current_option = option
        return True

    async def async_select_option(self, option: str) -> None:
        """Send a new option to the device and adopt it optimistically."""
        self._attr_current_option = option
        await self._write_optimistic(option)


async_setup_entry = binding_platform_setup(Platform.SELECT, ESPWeaverSelect)
