# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver button entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import Platform

from .entity import ESPWeaverBindingEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverButton(ESPWeaverBindingEntity, ButtonEntity):
    """A one-shot action exposed as a button.

    A boolean parameter the device acts on when written true; nothing to read.
    """

    _device_class_enum = ButtonDeviceClass

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Ignore reports: a button has no state to adopt."""
        return False

    async def async_press(self) -> None:
        """Ask the device to perform the action."""
        await self._write(True)


async_setup_entry = binding_platform_setup(Platform.BUTTON, ESPWeaverButton)
