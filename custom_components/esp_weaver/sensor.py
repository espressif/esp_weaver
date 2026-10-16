# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver sensor entity, driven entirely by device profiles."""

import asyncio
import contextlib
import logging
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import Platform
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    ALERT_KIND_LEVEL,
    ALERT_KIND_THRESHOLD,
    EVENT_ALERT,
    THRESHOLD_ALERT_HIGH,
    THRESHOLD_ALERT_LOW,
)
from .entity import ESPWeaverBindingEntity
from .helpers.binding_types import state_class_of
from .helpers.discovery import binding_platform_setup
from .helpers.templating import render_template
from .helpers.thresholds import as_float, threshold_entity_id, threshold_value
from .helpers.translations import async_common_messages

_LOGGER = logging.getLogger(__name__)

# Read-only: the coordinator alone updates these entities.
PARALLEL_UPDATES: Final = 0


class ESPWeaverSensor(ESPWeaverBindingEntity, SensorEntity):
    """A read-only reading whose meaning comes from a device profile.

    The profile decides which parameter holds the state, which enum values it
    may take, and which companions are shown as attributes.
    """

    _device_class_enum = SensorDeviceClass

    def _configure(self) -> None:
        """Set up the sensor."""
        binding = self._binding
        self._attr_state_class = state_class_of(binding, SensorStateClass)
        self._attr_native_unit_of_measurement = binding.unit
        if binding.display_precision is not None:
            self._attr_suggested_display_precision = binding.display_precision
        # Home Assistant only accepts a value list on an enum sensor, and
        # requires one there, so the two travel together.
        if self._attr_device_class == SensorDeviceClass.ENUM and binding.options:
            self._attr_options = list(binding.options)

        primary = binding.primary
        self._primary = primary.attribute if primary else None
        # The companion attributes, kept beside the read-only detail the base
        # entity adopts, since both are read back as attributes.
        self._detail = [name for name in binding.attributes if name != self._primary]
        # Companions that override the state at a given value (a gesture
        # sensor reads "off" while Power is false).
        self._overrides = [
            (name, param.spec.state_when)
            for name, param in binding.attributes.items()
            if name != self._primary and param.spec.state_when
        ]

        self._reading: Any = None
        self._unlisted: set[Any] = set()
        self._threshold_task: asyncio.Task[None] | None = None
        # Which threshold alert is active, so a reading that stays out of
        # range is not announced on every report.
        self._threshold_alert: str | None = None
        # The threshold numbers being watched, and how to stop watching them.
        self._watched_numbers: frozenset[str] = frozenset()
        self._unwatch_numbers: Any = None
        self._auto_reset_task: asyncio.Task[None] | None = None
        self._alert_level: str | None = None
        self._live = False

    @property
    def native_value(self) -> Any:
        """Return the reading, unless a companion parameter overrides it."""
        for name, when in self._overrides:
            value = self._extra.get(name)
            if value is not None and value in when:
                return self._listed(when[value])
        return self._listed(self._reading)

    def _listed(self, value: Any) -> Any:
        """Reduce a value an enum sensor cannot represent to unknown.

        Home Assistant raises on one, taking the platform down; firmware does
        report values outside its declared set, "" before the first report
        being the usual one.
        """
        options = self.options
        if options is None or value is None or value in options:
            return value

        if value not in self._unlisted:
            self._unlisted.add(value)
            _LOGGER.warning(
                "Sensor %s reported %r, which the profile does not list as one "
                "of %s; reporting unknown",
                self.entity_id or self._binding.key,
                value,
                options,
            )
        return None

    async def async_added_to_hass(self) -> None:
        """Start acting on reports, judging the seeded state too."""
        await super().async_added_to_hass()
        self._live = True
        self._raise_or_clear_alert()
        self._check_thresholds()
        self._schedule_auto_reset(self._reading)

    async def async_will_remove_from_hass(self) -> None:
        """Drop anything still in flight when the entity goes away."""
        self._live = False
        self._cancel_auto_reset()
        await self._cancel_threshold_check()
        self._unwatch_threshold_numbers()
        await super().async_will_remove_from_hass()

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the reading and any companion parameters the report carries."""
        changed = self._adopt(self._detail, reported, self._extra)
        value = None if self._primary is None else self._read(self._primary, reported)

        if value is not None:
            # Restarted even when the value repeats, so a second gesture of the
            # same kind is shown in full rather than for whatever time was left.
            self._schedule_auto_reset(value)

            if value != self._reading:
                self._reading = value
                self._check_thresholds()
                changed = True

        # Last, so that an alert message quoting the state quotes the new one.
        self._raise_or_clear_alert()
        return changed

    def _attribute_value(self, name: str) -> Any:
        """Return one bound attribute's current value, state included."""
        if name == self._primary:
            return self._reading
        return self._extra.get(name)

    def _schedule_auto_reset(self, value: Any) -> None:
        """Arrange for a momentary reading to lapse back to its resting value."""
        auto_reset = self._binding.auto_reset
        if auto_reset is None or not self._live:
            return

        self._cancel_auto_reset()
        if value is None or value == auto_reset.to:
            return

        self._auto_reset_task = self.hass.async_create_task(
            self._reset_after(auto_reset.after, auto_reset.to),
            name=f"esp_weaver_auto_reset_{self._binding.key}",
        )

    async def _reset_after(self, delay: float, resting: Any) -> None:
        """Wait out the display duration, then return to the resting value."""
        await asyncio.sleep(delay)
        self._auto_reset_task = None
        if self._live and self._reading != resting:
            self._reading = resting
            self.async_write_ha_state()

    def _cancel_auto_reset(self) -> None:
        """Stop a pending reset, because a newer reading supersedes it."""
        task = self._auto_reset_task
        self._auto_reset_task = None
        if task is not None and not task.done():
            task.cancel()

    # --- Alerts ----------------------------------------------------------------
    #
    # Alerts - a profile-declared level, or a reading past a threshold number -
    # are fired as events, one at start and one at end, with the rendered
    # title and message; what to do about them is the user's automation.

    def _fire_alert(self, kind: str, active: bool, **data: Any) -> None:
        """Announce an alert beginning or ending, on the bus."""
        self.hass.bus.async_fire(
            EVENT_ALERT,
            {
                "kind": kind,
                "active": active,
                "node_id": self._node_id,
                "device": self._device_name,
                "entity_id": self.entity_id,
                "sensor": self._binding.name,
                **data,
            },
        )

    def _raise_or_clear_alert(self) -> None:
        """Announce entering a declared alert level, and leaving it again."""
        alert = self._binding.alert
        if alert is None or not self._live:
            return

        raw = self._attribute_value(alert.attribute)
        level = None if raw is None else str(raw)
        if level == self._alert_level:
            return
        previous, self._alert_level = self._alert_level, level

        if previous is not None and previous in alert.levels:
            self._fire_alert(ALERT_KIND_LEVEL, False, level=previous)
            _LOGGER.debug(
                "%s left alert level %s", self.entity_id or self._binding.key, previous
            )

        if level is None or (body := alert.levels.get(level)) is None:
            return

        fields = {
            "device": self._device_name,
            "state": self.native_value,
            "level": level,
        }
        try:
            title = render_template(body.title, fields)
            message = render_template(body.message, fields)
        except (KeyError, ValueError):
            _LOGGER.error(
                "Profile entity %r declares an alert message with an unknown "
                "placeholder; known ones are %s",
                self._binding.name,
                ", ".join(sorted(fields)),
            )
            return

        self._fire_alert(
            ALERT_KIND_LEVEL,
            True,
            level=level,
            state=self.native_value,
            title=title,
            message=message,
        )
        _LOGGER.info("%s: %s", title, message)

    # --- Thresholds the user sets through paired number entities -------------

    def _check_thresholds(self) -> None:
        """Judge the reading against its thresholds, as a task; a newer reading supersedes it."""
        if not self._binding.thresholds or not self._live:
            return
        if self._threshold_task is not None:
            self._threshold_task.cancel()
        self._threshold_task = self.hass.async_create_task(
            self._async_check_thresholds(),
            name=f"esp_weaver_threshold_check_{self._binding.key}",
        )

    async def _cancel_threshold_check(self) -> None:
        """Stop a running threshold check and wait for it to unwind."""
        task = self._threshold_task
        self._threshold_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _async_check_thresholds(self) -> None:
        """Announce the alert a reading has earned, or the end of the one active."""
        try:
            self._watch_threshold_numbers()
            alert, limit, unit = self._threshold_violation()
            if alert == self._threshold_alert:
                return
            previous, self._threshold_alert = self._threshold_alert, alert
            if previous is not None:
                self._fire_alert(ALERT_KIND_THRESHOLD, False, end=previous)
            if alert is not None and limit is not None:
                await self._announce_threshold_alert(alert, limit, unit)
        finally:
            if self._threshold_task is asyncio.current_task():
                self._threshold_task = None

    def _watch_threshold_numbers(self) -> None:
        """Judge the reading again whenever one of its threshold numbers changes.

        The numbers are looked up each time; they may not be registered yet
        when the sensor is added.
        """
        entity_ids = frozenset(
            entity_id
            for key in self._binding.thresholds.values()
            if (entity_id := threshold_entity_id(self.hass, self._node_id, key))
            is not None
        )
        if entity_ids == self._watched_numbers:
            return
        self._unwatch_threshold_numbers()
        self._watched_numbers = entity_ids
        if entity_ids:
            self._unwatch_numbers = async_track_state_change_event(
                self.hass, entity_ids, self._threshold_number_changed
            )

    def _unwatch_threshold_numbers(self) -> None:
        """Stop watching the threshold numbers."""
        if self._unwatch_numbers is not None:
            self._unwatch_numbers()
            self._unwatch_numbers = None
        self._watched_numbers = frozenset()

    @callback
    def _threshold_number_changed(self, event: Event[EventStateChangedData]) -> None:
        """A threshold moved: the reading may have crossed it, or come back."""
        if self._live:
            self._check_thresholds()

    def _threshold_violation(self) -> tuple[str | None, float | None, str]:
        """Return which end the reading is past, that end's value, and the unit.

        Compared as displayed, so both sides are in the user's units.
        """
        value = as_float(self.state)
        unit = self.unit_of_measurement or ""
        if value is None:
            return None, None, unit

        limits = {
            end: limit
            for end, key in self._binding.thresholds.items()
            if (limit := threshold_value(self.hass, self._node_id, key)) is not None
        }
        low, high = limits.get("min"), limits.get("max")
        if low is not None and high is not None and low >= high:
            _LOGGER.warning(
                "Thresholds of %s are the wrong way round: min %s >= max %s",
                self.entity_id or self._binding.key,
                low,
                high,
            )
            return None, None, unit
        if high is not None and value > high:
            return THRESHOLD_ALERT_HIGH, high, unit
        if low is not None and value < low:
            return THRESHOLD_ALERT_LOW, low, unit
        return None, None, unit

    async def _announce_threshold_alert(
        self, end: str, limit: float, unit: str
    ) -> None:
        """Announce one threshold alert, worded in the server's language."""
        title_key, message_key = f"threshold_{end}_title", f"threshold_{end}_message"
        text = await async_common_messages(self.hass, title_key, message_key)
        fields = {
            "device": self._device_name,
            "sensor": self._binding.name,
            "value": self.state,
            "threshold": limit,
            "unit": unit,
        }
        title = render_template(text[title_key], fields)
        message = render_template(text[message_key], fields)
        self._fire_alert(
            ALERT_KIND_THRESHOLD,
            True,
            end=end,
            value=self.state,
            threshold=limit,
            unit=unit,
            title=title,
            message=message,
        )
        _LOGGER.info("%s: %s", title, message)


async_setup_entry = binding_platform_setup(Platform.SENSOR, ESPWeaverSensor)
