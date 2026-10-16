# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for reading and writing the dates and times a device carries as text."""

from datetime import UTC, date, datetime, time
import logging
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.helpers.moments import (
    format_datetime,
    parse_date,
    parse_datetime,
    parse_time,
)


class TestParseDate:
    """A calendar date arrives as ISO text."""

    def test_reads_iso_text(self) -> None:
        """The plain form firmware writes."""
        assert parse_date("2026-03-04") == date(2026, 3, 4)

    @pytest.mark.parametrize("raw", ["yesterday", 20260304, "", "2026-13-01"])
    def test_anything_else_is_unknown_and_said(
        self, raw: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A value that is not a date is dropped, not guessed at."""
        caplog.set_level(logging.WARNING)

        assert parse_date(raw) is None

        assert "not a usable date" in caplog.text

    def test_nothing_reported_is_quiet(self, caplog: pytest.LogCaptureFixture) -> None:
        """None is a parameter that has not spoken yet, not a bad value."""
        caplog.set_level(logging.WARNING)

        assert parse_date(None) is None

        assert caplog.text == ""


class TestParseTime:
    """A time of day arrives as ISO text."""

    def test_reads_iso_text(self) -> None:
        """Seconds are optional in what firmware writes."""
        assert parse_time("07:30") == time(7, 30)
        assert parse_time("07:30:15") == time(7, 30, 15)

    def test_anything_else_is_unknown(self, caplog: pytest.LogCaptureFixture) -> None:
        """A value that is not a time is dropped."""
        caplog.set_level(logging.WARNING)

        assert parse_time("noon") is None

        assert "not a usable time" in caplog.text


class TestParseDatetime:
    """An instant arrives as seconds since the epoch or as text."""

    def test_a_number_is_seconds_since_the_epoch(self) -> None:
        """Firmware with a clock reports a timestamp."""
        assert parse_datetime(0) == datetime(1970, 1, 1, tzinfo=UTC)
        assert parse_datetime(86400.0) == datetime(1970, 1, 2, tzinfo=UTC)

    def test_text_with_an_offset_keeps_it(self) -> None:
        """An instant that says where it is stays there."""
        parsed = parse_datetime("2026-03-04T07:30:00+02:00")

        assert parsed is not None
        assert parsed.utcoffset() is not None
        assert parsed.astimezone(UTC) == datetime(2026, 3, 4, 5, 30, tzinfo=UTC)

    async def test_text_without_an_offset_is_read_in_the_home_zone(
        self, hass: HomeAssistant
    ) -> None:
        """A wall-clock time is the one the house keeps."""
        await hass.config.async_set_time_zone("Asia/Shanghai")

        parsed = parse_datetime("2026-03-04T07:30:00")

        assert parsed == datetime(2026, 3, 4, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    @pytest.mark.parametrize("raw", [True, "later", 1e300])
    def test_anything_else_is_unknown_and_said(
        self, raw: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bool, prose or an unset clock's garbage are all dropped, not raised."""
        caplog.set_level(logging.WARNING)

        assert parse_datetime(raw) is None

        assert "not a usable datetime" in caplog.text


class TestFormatDatetime:
    """What goes back to the device."""

    def test_an_epoch_parameter_gets_whole_seconds(self) -> None:
        """A timestamp parameter counts seconds."""
        instant = datetime(2026, 3, 4, 5, 30, 45, 999000, tzinfo=UTC)

        assert format_datetime(instant, as_epoch=True) == 1772602245

    async def test_a_text_parameter_gets_the_home_zone_with_its_offset(
        self, hass: HomeAssistant
    ) -> None:
        """Written on the same wall clock the date and time entities use."""
        await hass.config.async_set_time_zone("Asia/Shanghai")
        instant = datetime(2026, 3, 4, 5, 30, tzinfo=UTC)

        assert format_datetime(instant, as_epoch=False) == "2026-03-04T13:30:00+08:00"
