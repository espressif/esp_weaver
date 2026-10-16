# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver text entity, driven entirely by device profiles."""

import logging
from typing import Any, Final

from homeassistant.components.text import TextEntity
from homeassistant.const import Platform

from .entity import ESPWeaverBindingEntity
from .helpers.discovery import binding_platform_setup

_LOGGER = logging.getLogger(__name__)

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# RainMaker declares no length; Home Assistant insists on one. Larger than
# any value a profile carries today (a POSIX timezone string is the longest).
MAX_LENGTH: Final = 255


class ESPWeaverText(ESPWeaverBindingEntity, TextEntity):
    """A writable string parameter exposed as a text entity."""

    _attr_native_max = MAX_LENGTH

    def _configure(self) -> None:
        """Set up the text entity."""
        self._attr_native_value = None

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the string the device reported."""
        value = self._read_primary(reported)
        if value is None:
            return False

        value = str(value)
        if len(value) > MAX_LENGTH:
            _LOGGER.warning(
                "Device %s reported %d characters for %s, keeping the first %d",
                self._node_id,
                len(value),
                self._binding.name,
                MAX_LENGTH,
            )
            value = value[:MAX_LENGTH]

        if value == self._attr_native_value:
            return False

        self._attr_native_value = value
        return True

    async def async_set_value(self, value: str) -> None:
        """Send a new string to the device and adopt it optimistically."""
        self._attr_native_value = value
        await self._write_optimistic(value)


async_setup_entry = binding_platform_setup(Platform.TEXT, ESPWeaverText)
