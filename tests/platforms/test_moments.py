# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the date, time and datetime platforms and their conversions."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.esp_weaver.date import ESPWeaverDate
from custom_components.esp_weaver.datetime import ESPWeaverDateTime
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.time import ESPWeaverTime
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Schedule"


def _binding(
    platform: str, param: str, data_type: str = "string", **overrides: Any
) -> EntityBinding:
    """Build a writable single-parameter binding for one of the three platforms."""
    spec = AttributeSpec(
        name="value",
        param=param,
        data_type=data_type,
        properties=("read", "write"),
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.other",
        "platform": platform,
        "name": param,
        "key": platform,
        "attributes": {
            "value": ParamBinding(device_name=DEVICE, attribute="value", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


class TestDate:
    """A calendar date the device keeps as text."""

    PARAM = "Holiday Start"

    def _entity(
        self,
        coordinator: MagicMock,
        hass: HomeAssistant | None = None,
        report: dict[str, Any] | None = None,
    ) -> ESPWeaverDate:
        entity = ESPWeaverDate(
            coordinator,
            _binding("date", self.PARAM),
            TEST_DEVICE_NAME,
            report=report,
        )
        if hass is not None:
            entity.hass = hass
            entity.entity_id = "date.test"
        return entity

    def test_reads_the_reported_date(self, coordinator: MagicMock) -> None:
        """The text the device carries becomes a real date."""
        entity = self._entity(coordinator, report={self.PARAM: "2026-08-27"})

        assert entity.native_value == date(2026, 8, 27)

    def test_unusable_text_leaves_the_value_unknown(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed setpoint is a firmware bug, not a reason to crash."""
        entity = self._entity(coordinator, report={self.PARAM: "27/08/2026"})

        assert entity.native_value is None
        assert "not a usable date" in caplog.text

    async def test_writes_it_back_as_text(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The device is given the same shape it reports."""
        entity = self._entity(coordinator, hass)

        await entity.async_set_value(date(2026, 12, 24))

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {self.PARAM: "2026-12-24"}}
        )
        assert entity.native_value == date(2026, 12, 24)


class TestTime:
    """A time of day the device keeps as text."""

    PARAM = "Cycle Start"

    def _entity(
        self,
        coordinator: MagicMock,
        hass: HomeAssistant | None = None,
        report: dict[str, Any] | None = None,
    ) -> ESPWeaverTime:
        entity = ESPWeaverTime(
            coordinator,
            _binding("time", self.PARAM),
            TEST_DEVICE_NAME,
            report=report,
        )
        if hass is not None:
            entity.hass = hass
            entity.entity_id = "time.test"
        return entity

    def test_reads_the_reported_time(self, coordinator: MagicMock) -> None:
        """The text the device carries becomes a real time."""
        entity = self._entity(coordinator, report={self.PARAM: "06:30:00"})

        assert entity.native_value == time(6, 30)

    def test_unusable_text_leaves_the_value_unknown(
        self, coordinator: MagicMock
    ) -> None:
        """A malformed setpoint is a firmware bug, not a reason to crash."""
        entity = self._entity(coordinator, report={self.PARAM: "half six"})

        assert entity.native_value is None

    async def test_writes_it_back_as_text(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The device is given the same shape it reports."""
        entity = self._entity(coordinator, hass)

        await entity.async_set_value(time(21, 15))

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {self.PARAM: "21:15:00"}}
        )


class TestDateTime:
    """An instant, carried either as text or as epoch seconds."""

    PARAM = "Next Run"

    def _entity(
        self,
        coordinator: MagicMock,
        hass: HomeAssistant | None = None,
        report: dict[str, Any] | None = None,
        data_type: str = "string",
    ) -> ESPWeaverDateTime:
        entity = ESPWeaverDateTime(
            coordinator,
            _binding("datetime", self.PARAM, data_type),
            TEST_DEVICE_NAME,
            report=report,
        )
        if hass is not None:
            entity.hass = hass
            entity.entity_id = "datetime.test"
        return entity

    def test_reads_an_iso_timestamp(self, coordinator: MagicMock) -> None:
        """A timestamp with an offset is taken as it stands."""
        entity = self._entity(
            coordinator, report={self.PARAM: "2026-08-27T06:30:00+08:00"}
        )

        assert entity.native_value == datetime(
            2026, 8, 27, 6, 30, tzinfo=timezone(timedelta(hours=8))
        )

    def test_text_without_an_offset_is_read_in_the_local_zone(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Home Assistant refuses a naive instant, so a zone has to be assumed.

        Its own is the one to assume: it is what the date and time entities
        write, and a device in the same house is most likely keeping it too.
        """
        entity = self._entity(coordinator, report={self.PARAM: "2026-08-27T06:30:00"})

        assert entity.native_value == datetime(
            2026, 8, 27, 6, 30, tzinfo=dt_util.get_default_time_zone()
        )

    def test_an_epoch_beyond_what_a_date_can_hold_is_not_read(
        self, coordinator: MagicMock
    ) -> None:
        """A device whose clock was never set reports a number nothing can hold.

        This is read in a bus callback, so an exception here would stop the
        entity updating from then on.
        """
        entity = self._entity(coordinator, report={self.PARAM: 10**20}, data_type="int")

        assert entity.native_value is None

    def test_reads_epoch_seconds(self, coordinator: MagicMock) -> None:
        """Firmware that keeps a clock reports a count, not a string."""
        entity = self._entity(
            coordinator, report={self.PARAM: 1767225600}, data_type="int"
        )

        assert entity.native_value == datetime.fromtimestamp(1767225600, UTC)

    def test_a_boolean_is_not_an_instant(self, coordinator: MagicMock) -> None:
        """True would otherwise read as one second past the epoch."""
        entity = self._entity(coordinator, report={self.PARAM: True}, data_type="int")

        assert entity.native_value is None

    async def test_writes_text_back_in_the_local_zone(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The same zone the date and time entities write in."""
        entity = self._entity(coordinator, hass)

        await entity.async_set_value(
            datetime(2026, 8, 27, 6, 30, tzinfo=timezone(timedelta(hours=8)))
        )

        written = coordinator.api.set_params.await_args[0][1][DEVICE][self.PARAM]

        parsed = dt_util.parse_datetime(written)
        assert parsed is not None
        assert parsed == datetime(
            2026, 8, 27, 6, 30, tzinfo=timezone(timedelta(hours=8))
        )
        assert (
            parsed.utcoffset()
            == datetime(2026, 8, 27, tzinfo=dt_util.get_default_time_zone()).utcoffset()
        )

    async def test_writes_epoch_seconds_when_that_is_what_the_parameter_holds(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A numeric parameter gets a number back."""
        entity = self._entity(coordinator, hass, data_type="int")

        await entity.async_set_value(datetime(2026, 1, 1, tzinfo=UTC))

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {self.PARAM: 1767225600}}
        )
