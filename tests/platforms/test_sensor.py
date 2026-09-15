# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven sensor platform."""

import asyncio
from collections.abc import Generator, Mapping
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.esp_weaver.const import (
    ALERT_KIND_LEVEL,
    ALERT_KIND_THRESHOLD,
    EVENT_ALERT,
)
from custom_components.esp_weaver.helpers.signals import signal_binding_discovered
from custom_components.esp_weaver.identity import DOMAIN, entity_unique_id
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import (
    AlertLevelSpec,
    AlertSpec,
    AttributeSpec,
    AutoResetSpec,
)
from custom_components.esp_weaver.sensor import ESPWeaverSensor, async_setup_entry
from tests.conftest import (
    TEST_DEVICE_NAME,
    TEST_NODE_ID,
    AddedEntities,
    create_mock_config_entry,
    record_state_writes,
    state_writes,
)

DEVICE = "Sensor"

# Firmware parameter names, deliberately unlike the HA attribute names.
TEMPERATURE = "Temperature"
BATTERY_LEVEL = "Battery Level"
ALERT_LEVEL = "Alert Level"
VOLTAGE = "Voltage"
GESTURE = "Gesture Type"
POWER = "Power"
SLEEP_STATE = "Sleep State"
WAKE_REASON = "Wake Reason"


def _param(attribute: str, param: str, **spec: Any) -> ParamBinding:
    """Build one read-only attribute binding, as a sensor's always are."""
    spec.setdefault("properties", ("read",))
    return ParamBinding(
        device_name=DEVICE,
        attribute=attribute,
        spec=AttributeSpec(name=attribute, param=param, **spec),
    )


def _binding(attributes: dict[str, ParamBinding], **overrides: Any) -> EntityBinding:
    """Build a sensor binding out of the given attributes."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.sensor",
        "platform": "sensor",
        "name": "Temperature",
        "key": "temperature",
        "attributes": attributes,
    }
    return EntityBinding(**{**defaults, **overrides})


def _temperature(**overrides: Any) -> EntityBinding:
    """The plainest sensor there is: one float with a unit."""
    declared: dict[str, Any] = {
        "device_class": "temperature",
        "state_class": "measurement",
        "unit": "°C",
        "display_precision": 1,
    }
    return _binding(
        {"state": _param("state", TEMPERATURE, data_type="float")},
        **{**declared, **overrides},
    )


def _battery() -> EntityBinding:
    """A battery whose alert level is announced, as the profile says."""
    return _binding(
        {"state": _param("state", BATTERY_LEVEL, data_type="int")},
        name="Battery",
        key="battery_energy",
        device_type="esp.device.battery-energy",
        device_class="battery",
        state_class="measurement",
        unit="%",
        extra_attributes={
            "voltage": _param("voltage", VOLTAGE, data_type="float"),
            "alert_level": _param(
                "alert_level", ALERT_LEVEL, options=("normal", "low", "critical")
            ),
        },
        alert=AlertSpec(
            attribute="alert_level",
            levels={
                "critical": AlertLevelSpec(
                    title="{device} - Critical Battery",
                    message="Battery critical: {state}% - Charge immediately!",
                ),
                "low": AlertLevelSpec(
                    title="{device} - Low Battery",
                    message="Battery low: {state}% - Please charge soon.",
                ),
            },
        ),
    )


def _gesture() -> EntityBinding:
    """A momentary reading that lapses back to idle and obeys its Power."""
    return _binding(
        {
            "state": _param("state", GESTURE, data_type="string"),
            "power": _param(
                "power",
                POWER,
                data_type="bool",
                properties=("read", "write"),
                state_when={False: "off"},
            ),
        },
        name="IMU Gesture",
        key="imu_gesture",
        device_type="esp.device.imu-gesture",
        auto_reset=AutoResetSpec(to="idle", after=0.01),
    )


def _sleep() -> EntityBinding:
    """An enum reading with one companion attribute."""
    return _binding(
        {"state": _param("state", SLEEP_STATE, data_type="string")},
        name="Sleep State",
        key="low_power_sleep",
        device_type="esp.device.low-power-sleep",
        device_class="enum",
        options=("awake", "light_sleep", "deep_sleep"),
        extra_attributes={"wake_reason": _param("wake_reason", WAKE_REASON)},
    )


def _sensor(
    coordinator: MagicMock,
    binding: EntityBinding,
    report: dict[str, Any] | None = None,
    hass: HomeAssistant | None = None,
) -> ESPWeaverSensor:
    """Build a sensor entity that is safe to drive without a real platform."""
    entity = ESPWeaverSensor(coordinator, binding, TEST_DEVICE_NAME, report=report)
    if hass is not None:
        entity.hass = hass
    entity.entity_id = "sensor.test"
    record_state_writes(entity)
    return entity


async def _live_sensor(
    hass: HomeAssistant,
    coordinator: MagicMock,
    binding: EntityBinding,
    report: dict[str, Any] | None = None,
) -> ESPWeaverSensor:
    """Build a sensor that has been added to hass, so it acts on reports."""
    entity = _sensor(coordinator, binding, report=report, hass=hass)
    await entity.async_added_to_hass()
    return entity


def _report(entity: ESPWeaverSensor, **params: Any) -> None:
    """Deliver a parameter report for this sensor's device instance."""
    entity._handle_params_update({DEVICE: params})


class TestSensorIdentity:
    """Identity and presentation come from the profile."""

    def test_unique_id_comes_from_the_profile_key(self, coordinator: MagicMock) -> None:
        """Renaming a sensor in the profile must not orphan the entity."""
        entity = _sensor(coordinator, _temperature())

        assert entity.unique_id == f"{TEST_NODE_ID}_temperature"
        assert entity.translation_key == "temperature"
        assert entity.name == "Temperature"

    def test_measurement_metadata_comes_from_the_profile(
        self, coordinator: MagicMock
    ) -> None:
        """Device class, state class, unit and precision are all declared."""
        entity = _sensor(coordinator, _temperature())

        assert entity.device_class == SensorDeviceClass.TEMPERATURE
        assert entity.state_class == SensorStateClass.MEASUREMENT
        assert entity.native_unit_of_measurement == "°C"
        assert entity.suggested_display_precision == 1

    def test_an_unknown_state_class_is_dropped_not_fatal(
        self,
        coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A typo in the profile costs the metadata, not the entity."""
        entity = _sensor(coordinator, _temperature(state_class="averaged"))

        assert entity.state_class is None
        assert "averaged" in caplog.text

    def test_enum_sensors_carry_their_value_list(self, coordinator: MagicMock) -> None:
        """Home Assistant requires the options on an enum sensor."""
        entity = _sensor(coordinator, _sleep())

        assert entity.device_class == SensorDeviceClass.ENUM
        assert entity.options == ["awake", "light_sleep", "deep_sleep"]

    def test_a_value_outside_the_options_reads_unknown(
        self,
        coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An enum sensor must not raise on a value the profile did not list."""
        entity = _sensor(coordinator, _sleep(), report={SLEEP_STATE: ""})

        assert entity.native_value is None
        assert "does not list" in caplog.text

    def test_a_listed_value_survives(self, coordinator: MagicMock) -> None:
        """The guard only drops values outside the list."""
        entity = _sensor(coordinator, _sleep(), report={SLEEP_STATE: "deep_sleep"})

        assert entity.native_value == "deep_sleep"

    def test_options_without_an_enum_device_class_are_left_off(
        self, coordinator: MagicMock
    ) -> None:
        """The gesture sensor lists its gestures as documentation only."""
        entity = _sensor(
            coordinator, _binding({"state": _param("state", GESTURE)}, options=("a",))
        )

        assert entity.options is None


class TestSensorReads:
    """Turning device reports into Home Assistant state."""

    def test_seeds_itself_from_the_discovery_report(
        self, coordinator: MagicMock
    ) -> None:
        """A new sensor starts with what the node already published."""
        entity = _sensor(coordinator, _temperature(), report={TEMPERATURE: 21.5})

        assert entity.native_value == 21.5

    def test_adopts_a_later_report(self, coordinator: MagicMock) -> None:
        """The reading follows the parameter it is bound to."""
        entity = _sensor(coordinator, _temperature(), report={TEMPERATURE: 21.5})

        _report(entity, **{TEMPERATURE: 23.0})

        assert entity.native_value == 23.0
        state_writes(entity).assert_called_once()

    def test_ignores_a_report_that_does_not_mention_it(
        self, coordinator: MagicMock
    ) -> None:
        """Another parameter changing is not this sensor's business."""
        entity = _sensor(coordinator, _temperature(), report={TEMPERATURE: 21.5})

        _report(entity, Humidity=60)

        assert entity.native_value == 21.5
        state_writes(entity).assert_not_called()

    def test_repeating_the_same_reading_writes_no_state(
        self, coordinator: MagicMock
    ) -> None:
        """An unchanged value should not wake the recorder."""
        entity = _sensor(coordinator, _temperature(), report={TEMPERATURE: 21.5})

        _report(entity, **{TEMPERATURE: 21.5})

        state_writes(entity).assert_not_called()

    def test_companion_parameters_become_attributes(
        self, coordinator: MagicMock
    ) -> None:
        """Everything bound but not the state is shown as detail."""
        entity = _sensor(
            coordinator,
            _battery(),
            report={BATTERY_LEVEL: 80, VOLTAGE: 3.9, ALERT_LEVEL: "normal"},
        )

        attrs = entity.extra_state_attributes
        assert attrs["voltage"] == 3.9
        assert attrs["alert_level"] == "normal"
        assert entity.native_value == 80

    def test_attributes_are_only_what_the_profile_bound(
        self, coordinator: MagicMock
    ) -> None:
        """Nothing about thresholds or parameter names leaks into attributes."""
        entity = _sensor(coordinator, _temperature(), report={TEMPERATURE: 21.5})

        assert entity.extra_state_attributes == {}


class TestStateWhen:
    """A companion parameter can speak for the state."""

    def test_power_off_overrides_the_last_gesture(self, coordinator: MagicMock) -> None:
        """A gesture sensor that is switched off reports off, not a gesture."""
        entity = _sensor(
            coordinator, _gesture(), report={GESTURE: "shake", POWER: False}
        )

        assert entity.native_value == "off"

    def test_power_on_lets_the_gesture_through(self, coordinator: MagicMock) -> None:
        """With power on there is nothing to override."""
        entity = _sensor(
            coordinator, _gesture(), report={GESTURE: "shake", POWER: True}
        )

        assert entity.native_value == "shake"

    def test_an_unreported_power_leaves_the_state_alone(
        self, coordinator: MagicMock
    ) -> None:
        """Firmware that never mentions Power must not be read as off."""
        entity = _sensor(coordinator, _gesture(), report={GESTURE: "shake"})

        assert entity.native_value == "shake"


class TestAutoReset:
    """Momentary readings lapse back to their resting value."""

    async def test_a_gesture_lapses_back_to_idle(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Nothing tells the sensor a gesture ended, so it times itself out."""
        entity = await _live_sensor(hass, coordinator, _gesture())

        _report(entity, **{GESTURE: "shake", POWER: True})
        assert entity.native_value == "shake"

        # Awaited rather than slept past. Sleeping a little longer than the
        # timer is a race the test loses on a loaded machine, and it makes the
        # suite pay for a delay whose length is arbitrary anyway.
        pending = entity._auto_reset_task
        assert pending is not None
        await pending

        assert entity.native_value == "idle"

    async def test_a_new_gesture_restarts_the_clock(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A second gesture is shown in full, not for whatever time was left."""
        entity = await _live_sensor(hass, coordinator, _gesture())

        _report(entity, **{GESTURE: "shake", POWER: True})
        first = entity._auto_reset_task
        _report(entity, **{GESTURE: "shake", POWER: True})

        assert first is not None
        assert first.cancelled() or first is not entity._auto_reset_task

    async def test_the_resting_value_itself_starts_no_timer(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Reporting idle is already the destination."""
        entity = await _live_sensor(hass, coordinator, _gesture())

        _report(entity, **{GESTURE: "idle", POWER: True})

        assert entity._auto_reset_task is None

    async def test_removal_cancels_a_pending_reset(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A timer must not outlive the entity it would write to."""
        entity = await _live_sensor(hass, coordinator, _gesture())
        _report(entity, **{GESTURE: "shake", POWER: True})
        pending = entity._auto_reset_task

        await entity.async_will_remove_from_hass()
        await asyncio.sleep(0)

        assert pending is not None
        assert pending.cancelled()

    async def test_a_sensor_without_the_declaration_never_resets(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A temperature is not momentary and must hold its reading."""
        entity = await _live_sensor(hass, coordinator, _temperature())

        _report(entity, **{TEMPERATURE: 21.5})

        # No timer was started, so there is nothing that could yet change it -
        # which is a stronger statement than waiting a while and looking again.
        assert entity._auto_reset_task is None
        assert entity.native_value == 21.5


def _alerts(hass: HomeAssistant) -> list[Event]:
    """Start collecting the alert events the sensor fires from here on."""
    return async_capture_events(hass, EVENT_ALERT)


def _data(events: list[Event]) -> list[Mapping[str, Any]]:
    """The data of every captured event, in order."""
    return [event.data for event in events]


class TestAlerts:
    """A declared level is announced as an event on the bus.

    Announced, not shown: what to do about a low battery - a phone
    notification, a light, nothing - is the user's automation's decision.
    """

    async def test_entering_a_level_fires_an_alert(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Low battery is worth telling someone about, with the words the profile chose."""
        entity = await _live_sensor(hass, coordinator, _battery())
        events = _alerts(hass)

        _report(entity, **{BATTERY_LEVEL: 15, ALERT_LEVEL: "low"})
        await hass.async_block_till_done()

        assert _data(events) == [
            {
                "kind": ALERT_KIND_LEVEL,
                "active": True,
                "node_id": TEST_NODE_ID,
                "device": TEST_DEVICE_NAME,
                "entity_id": "sensor.test",
                "sensor": entity._binding.name,
                "level": "low",
                "state": 15,
                "title": f"{TEST_DEVICE_NAME} - Low Battery",
                "message": "Battery low: 15% - Please charge soon.",
            }
        ]

    async def test_returning_to_normal_ends_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A level outside the declared ones means the alert is over."""
        entity = await _live_sensor(hass, coordinator, _battery())
        _report(entity, **{BATTERY_LEVEL: 15, ALERT_LEVEL: "low"})
        events = _alerts(hass)

        _report(entity, **{BATTERY_LEVEL: 90, ALERT_LEVEL: "normal"})
        await hass.async_block_till_done()

        assert [(d["level"], d["active"]) for d in _data(events)] == [("low", False)]

    async def test_a_promotion_ends_the_lesser_level(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Going from low to critical is the end of one alert and the start of another."""
        entity = await _live_sensor(hass, coordinator, _battery())
        _report(entity, **{BATTERY_LEVEL: 15, ALERT_LEVEL: "low"})
        events = _alerts(hass)

        _report(entity, **{BATTERY_LEVEL: 3, ALERT_LEVEL: "critical"})
        await hass.async_block_till_done()

        assert [(d["level"], d["active"]) for d in _data(events)] == [
            ("low", False),
            ("critical", True),
        ]

    async def test_an_unchanged_level_is_not_repeated(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Every report while low must not announce the alert again."""
        entity = await _live_sensor(hass, coordinator, _battery())
        _report(entity, **{BATTERY_LEVEL: 15, ALERT_LEVEL: "low"})
        events = _alerts(hass)

        _report(entity, **{BATTERY_LEVEL: 14, ALERT_LEVEL: "low"})
        await hass.async_block_till_done()

        assert events == []

    async def test_a_device_already_in_trouble_alerts_on_startup(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The state seeded at discovery still deserves an announcement."""
        entity = _sensor(
            coordinator,
            _battery(),
            report={BATTERY_LEVEL: 3, ALERT_LEVEL: "critical"},
            hass=hass,
        )
        events = _alerts(hass)

        await entity.async_added_to_hass()
        await hass.async_block_till_done()

        assert [d["level"] for d in _data(events)] == ["critical"]

    async def test_a_sensor_without_the_declaration_never_alerts(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Most sensors have no alert levels at all."""
        entity = await _live_sensor(hass, coordinator, _temperature())
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: 99.0})
        await hass.async_block_till_done()

        assert events == []

    async def test_a_message_that_reaches_into_its_fields_is_refused(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Profile text may name a field, not walk into it (``{state.__class__}``)."""
        binding = _battery()
        assert binding.alert is not None
        levels = dict(binding.alert.levels)
        levels["low"] = AlertLevelSpec(
            title="{device}", message="{state.__class__.__mro__}"
        )
        binding = replace(binding, alert=replace(binding.alert, levels=levels))
        entity = await _live_sensor(hass, coordinator, binding)
        events = _alerts(hass)

        _report(entity, **{BATTERY_LEVEL: 15, ALERT_LEVEL: "low"})
        await hass.async_block_till_done()

        assert events == []


MIN_KEY = "temperature_min_threshold"
MAX_KEY = "temperature_max_threshold"
TRANSLATIONS = {
    f"component.{DOMAIN}.common.threshold_high_title": "{device}: {sensor} too high",
    f"component.{DOMAIN}.common.threshold_high_message": (
        "{sensor} reads {value}{unit}, above the {threshold}{unit} limit you set."
    ),
    f"component.{DOMAIN}.common.threshold_low_title": "{device}: {sensor} too low",
    f"component.{DOMAIN}.common.threshold_low_message": (
        "{sensor} reads {value}{unit}, below the {threshold}{unit} limit you set."
    ),
}


def _bounded_temperature(**thresholds: str) -> EntityBinding:
    """A temperature whose profile pairs it with threshold numbers."""
    return _temperature(
        thresholds=thresholds or {"min": MIN_KEY, "max": MAX_KEY},
    )


def _set_threshold(hass: HomeAssistant, key: str, value: Any) -> None:
    """Give a paired number entity a state, as its platform would."""
    entry = er.async_get(hass).async_get_or_create(
        NUMBER_DOMAIN, DOMAIN, entity_unique_id(TEST_NODE_ID, key)
    )
    hass.states.async_set(entry.entity_id, str(value), {"unit_of_measurement": "°C"})


class TestThresholdAlerts:
    """A reading past a number the user set is announced as an event."""

    @pytest.fixture(autouse=True)
    def _text(self) -> Generator[None]:
        with patch(
            "custom_components.esp_weaver.helpers.translations.async_get_translations",
            return_value=TRANSLATIONS,
        ):
            yield

    async def _bounded(
        self, hass: HomeAssistant, coordinator: MagicMock, **params: Any
    ) -> ESPWeaverSensor:
        _set_threshold(hass, MIN_KEY, 5.0)
        _set_threshold(hass, MAX_KEY, 30.0)
        entity = await _live_sensor(
            hass, coordinator, _bounded_temperature(), report=params or None
        )
        await hass.async_block_till_done()
        return entity

    async def test_a_reading_above_the_maximum_raises_the_high_alert(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The event carries the reading, the limit and the unit, and words for them."""
        entity = await self._bounded(hass, coordinator)
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: 35.0})
        await hass.async_block_till_done()

        assert _data(events) == [
            {
                "kind": ALERT_KIND_THRESHOLD,
                "active": True,
                "node_id": TEST_NODE_ID,
                "device": TEST_DEVICE_NAME,
                "entity_id": "sensor.test",
                "sensor": "Temperature",
                "end": "high",
                "value": 35.0,
                "threshold": 30.0,
                "unit": "°C",
                "title": f"{TEST_DEVICE_NAME}: Temperature too high",
                "message": "Temperature reads 35.0°C, above the 30.0°C limit you set.",
            }
        ]

    async def test_a_reading_below_the_minimum_raises_the_low_alert(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The other end of the range has its own text."""
        entity = await self._bounded(hass, coordinator)
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: 1.0})
        await hass.async_block_till_done()

        (data,) = _data(events)
        assert data["end"] == "low"
        assert "below the 5.0°C limit" in data["message"]

    async def test_staying_out_of_range_is_announced_once(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A sensor that keeps reading high does not nag on every report."""
        entity = await self._bounded(hass, coordinator)
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: 35.0})
        await hass.async_block_till_done()
        _report(entity, **{TEMPERATURE: 36.0})
        await hass.async_block_till_done()

        assert len(events) == 1

    async def test_coming_back_into_range_ends_the_alert(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The end is announced for the same end of the range the start was."""
        entity = await self._bounded(hass, coordinator)
        _report(entity, **{TEMPERATURE: 35.0})
        await hass.async_block_till_done()
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: 20.0})
        await hass.async_block_till_done()

        assert [(d["end"], d["active"]) for d in _data(events)] == [("high", False)]

    async def test_a_seeded_reading_is_judged_on_startup(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A device already out of range when Home Assistant starts says so."""
        events = _alerts(hass)

        await self._bounded(hass, coordinator, **{TEMPERATURE: 40.0})

        assert [d["end"] for d in _data(events)] == ["high"]

    async def test_moving_a_threshold_judges_the_reading_again(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A limit moved past the reading is an alert now, not at the next report."""
        entity = await self._bounded(hass, coordinator, **{TEMPERATURE: 25.0})
        events = _alerts(hass)

        _set_threshold(hass, MAX_KEY, 20.0)
        await hass.async_block_till_done()
        _set_threshold(hass, MAX_KEY, 30.0)
        await hass.async_block_till_done()

        assert [(d["end"], d["active"]) for d in _data(events)] == [
            ("high", True),
            ("high", False),
        ]
        assert entity._threshold_alert is None

    async def test_a_threshold_registered_late_is_watched_once_it_exists(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Numbers are set up alongside the sensor and may come after it."""
        entity = await _live_sensor(
            hass, coordinator, _bounded_temperature(), report={TEMPERATURE: 25.0}
        )
        await hass.async_block_till_done()
        assert entity._watched_numbers == frozenset()
        events = _alerts(hass)

        # The number turns up; the next report notices it and starts watching.
        _set_threshold(hass, MAX_KEY, 30.0)
        _report(entity, **{TEMPERATURE: 26.0})
        await hass.async_block_till_done()
        assert len(entity._watched_numbers) == 1

        _set_threshold(hass, MAX_KEY, 20.0)
        await hass.async_block_till_done()

        assert [d["end"] for d in _data(events)] == ["high"]

    async def test_a_missing_threshold_entity_disables_that_end(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Only the ends the user actually has numbers for are enforced."""
        _set_threshold(hass, MAX_KEY, 30.0)
        entity = await _live_sensor(hass, coordinator, _bounded_temperature())
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: -50.0})
        await hass.async_block_till_done()
        _report(entity, **{TEMPERATURE: 50.0})
        await hass.async_block_till_done()

        assert [d["end"] for d in _data(events)] == ["high"]

    async def test_crossed_thresholds_raise_nothing(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A range the wrong way round cannot be judged against."""
        _set_threshold(hass, MIN_KEY, 30.0)
        _set_threshold(hass, MAX_KEY, 5.0)
        entity = await _live_sensor(hass, coordinator, _bounded_temperature())
        events = _alerts(hass)

        _report(entity, **{TEMPERATURE: 50.0})
        await hass.async_block_till_done()

        assert events == []

    async def test_a_sensor_without_thresholds_never_looks(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Most sensors bound nothing, and must not touch the registry."""
        entity = await _live_sensor(hass, coordinator, _temperature())

        _report(entity, **{TEMPERATURE: 1000.0})
        await hass.async_block_till_done()

        assert entity._threshold_task is None
        assert entity._threshold_alert is None
        assert entity._watched_numbers == frozenset()

    async def test_removal_cancels_a_check_in_flight_and_stops_watching(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Nothing runs against an entity that has left Home Assistant."""
        entity = await self._bounded(hass, coordinator)
        assert entity._watched_numbers
        _report(entity, **{TEMPERATURE: 35.0})

        await entity.async_will_remove_from_hass()

        assert entity._threshold_task is None
        assert entity._watched_numbers == frozenset()
        events = _alerts(hass)
        _set_threshold(hass, MAX_KEY, 1.0)
        await hass.async_block_till_done()
        assert events == []


class TestSensorDiscovery:
    """The platform's binding discovery listener."""

    async def _setup(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> list[Any]:
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = coordinator
        entry.title = TEST_DEVICE_NAME
        added = AddedEntities()

        await async_setup_entry(hass, entry, added)
        return added

    async def test_creates_a_sensor_for_a_sensor_binding(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A sensor binding for this node produces one seeded entity."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _temperature(),
            {TEMPERATURE: 21.5},
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].native_value == 21.5

    async def test_ignores_bindings_for_other_platforms(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A switch binding is not a sensor."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _temperature(platform="switch"),
            {},
        )
        await hass.async_block_till_done()

        assert added == []

    async def test_the_same_binding_twice_creates_one_entity(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Rediscovery after a reconnect must not duplicate the sensor."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        for _ in range(2):
            async_dispatcher_send(
                hass,
                signal_binding_discovered(TEST_NODE_ID),
                _temperature(),
                {TEMPERATURE: 21.5},
            )
            await hass.async_block_till_done()

        assert len(added) == 1
