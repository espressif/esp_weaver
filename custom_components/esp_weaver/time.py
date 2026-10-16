# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver time entity, driven entirely by device profiles."""

from datetime import time
from typing import Any, Final

from homeassistant.components.time import TimeEntity
from homeassistant.const import Platform

from .entity import ESPWeaverMomentEntity
from .helpers.discovery import binding_platform_setup
from .helpers.moments import parse_time

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverTime(ESPWeaverMomentEntity[time], TimeEntity):
    """A time of day the device keeps as text, such as when to start a cycle."""

    def _parse(self, raw: Any) -> time | None:
        """Read a reported value as a time of day."""
        return parse_time(raw)

    def _format(self, value: time) -> str:
        """Write a time the way the device keeps it."""
        return value.isoformat()


async_setup_entry = binding_platform_setup(Platform.TIME, ESPWeaverTime)
