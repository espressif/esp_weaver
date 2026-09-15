# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver date entity, driven entirely by device profiles."""

from datetime import date
from typing import Any, Final

from homeassistant.components.date import DateEntity
from homeassistant.const import Platform

from .entity import ESPWeaverMomentEntity
from .helpers.discovery import binding_platform_setup
from .helpers.moments import parse_date

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverDate(ESPWeaverMomentEntity[date], DateEntity):
    """A date setpoint the device keeps as text, such as a holiday start."""

    def _parse(self, raw: Any) -> date | None:
        """Read a reported value as a calendar date."""
        return parse_date(raw)

    def _format(self, value: date) -> str:
        """Write a date the way the device keeps it."""
        return value.isoformat()


async_setup_entry = binding_platform_setup(Platform.DATE, ESPWeaverDate)
