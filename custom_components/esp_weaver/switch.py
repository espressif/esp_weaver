# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver switch entity, driven entirely by device profiles."""

from typing import Final

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import Platform

from .entity import ESPWeaverToggleEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverSwitch(ESPWeaverToggleEntity, SwitchEntity):
    """A switch whose parameter mapping comes from a device profile."""

    _device_class_enum = SwitchDeviceClass


async_setup_entry = binding_platform_setup(Platform.SWITCH, ESPWeaverSwitch)
