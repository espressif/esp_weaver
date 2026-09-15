# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reads and writes the dates, times and timestamps a device carries."""

from datetime import UTC, date, datetime, time, tzinfo
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .once import log_once

_LOGGER = logging.getLogger(__name__)


def parse_date(value: Any) -> date | None:
    """Read a reported value as a calendar date, or None if it is not one."""
    if isinstance(value, str) and (parsed := dt_util.parse_date(value)) is not None:
        return parsed
    _report(value, "date")
    return None


def parse_time(value: Any) -> time | None:
    """Read a reported value as a time of day, or None if it is not one."""
    if isinstance(value, str) and (parsed := dt_util.parse_time(value)) is not None:
        return parsed
    _report(value, "time")
    return None


def parse_datetime(value: Any) -> datetime | None:
    """Read a reported value as an instant, or None if it is not one.

    A number is epoch seconds. Text without an offset is read in Home
    Assistant's time zone, the one the date and time entities write in.
    """
    if isinstance(value, bool):
        _report(value, "datetime")
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            # An unset clock reports something outside the range a date holds.
            _report(value, "datetime")
            return None
    if isinstance(value, str) and (parsed := dt_util.parse_datetime(value)) is not None:
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_local_zone())
    _report(value, "datetime")
    return None


def format_datetime(value: datetime, as_epoch: bool) -> int | str:
    """Turn an instant into what the device's parameter expects.

    Written in Home Assistant's time zone, with the offset, so it agrees with
    what the date and time entities write.
    """
    if as_epoch:
        return int(value.timestamp())
    return value.astimezone(_local_zone()).isoformat(timespec="seconds")


def _local_zone() -> tzinfo:
    """Return the time zone Home Assistant is configured for."""
    return dt_util.get_default_time_zone()


def _report(value: Any, kind: str) -> None:
    """Note a value that could not be read, unless there was none."""
    if value is None:
        return
    # Once: a firmware that reports one bad value reports it on every poll.
    log_once(
        _LOGGER, logging.WARNING, "Reported value %r is not a usable %s", value, kind
    )
