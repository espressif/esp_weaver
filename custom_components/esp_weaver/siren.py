# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver siren entity, driven entirely by device profiles."""

from typing import Final

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.const import Platform

from .entity import ESPWeaverToggleEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverSiren(ESPWeaverToggleEntity, SirenEntity):
    """A siren whose parameter mapping comes from a device profile: on and off only."""

    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF


async_setup_entry = binding_platform_setup(Platform.SIREN, ESPWeaverSiren)
