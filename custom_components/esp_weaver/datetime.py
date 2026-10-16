# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver datetime entity, driven entirely by device profiles."""

from datetime import datetime
from typing import Any, Final

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.const import Platform

from .entity import ESPWeaverMomentEntity
from .helpers.discovery import binding_platform_setup
from .helpers.moments import format_datetime, parse_datetime

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# The data types that mean a count of seconds since the epoch rather than text.
EPOCH_TYPES: Final[frozenset[str]] = frozenset({"int", "float"})


class ESPWeaverDateTime(ESPWeaverMomentEntity[datetime], DateTimeEntity):
    """An instant the device carries, as an ISO timestamp or as epoch seconds."""

    def _configure(self) -> None:
        """Settle whether this parameter counts seconds or spells a timestamp."""
        primary = self._binding.primary
        self._epoch = primary is not None and primary.spec.data_type in EPOCH_TYPES

    def _parse(self, raw: Any) -> datetime | None:
        """Read a reported value as an instant."""
        return parse_datetime(raw)

    def _format(self, value: datetime) -> int | str:
        """Write an instant as whatever this device's parameter holds."""
        return format_datetime(value, self._epoch)


async_setup_entry = binding_platform_setup(Platform.DATETIME, ESPWeaverDateTime)
