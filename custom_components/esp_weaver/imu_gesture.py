# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver IMU gesture sensor entity."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    ATTR_ORIENTATION_CHANGE,
    ATTR_ORIENTATION_X,
    ATTR_ORIENTATION_Y,
    ATTR_ORIENTATION_Z,
    DEFAULT_GESTURE_DISPLAY_DURATION,
    ENTITY_NAME_IMU_GESTURE,
)
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import DiscoveryConfig, create_discovery_listener
from .iot.entity_states import GestureState
from .iot.specs.events import EVENT_IMU_GESTURE_DISCOVERED, EVENT_IMU_GESTURE_UPDATE
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_CONFIDENCE,
    KEY_GESTURE,
    KEY_GESTURE_DISPLAY_DURATION,
    KEY_ORIENTATION_CHANGE,
    KEY_ORIENTATION_X,
    KEY_ORIENTATION_Y,
    KEY_ORIENTATION_Z,
    KEY_POWER,
    KEY_SENSITIVITY,
    KEY_SENSOR_DATA,
    PLATFORM_TYPE_IMU_GESTURE,
)
from .iot.utils.gesture_utils import (
    GestureProcessor,
    GestureUpdateResult,
    get_gesture_display_name,
    get_gesture_icon,
)

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Shared gesture processor instance
_gesture_processor = GestureProcessor()


def setup_discovery_listener(
    hass: HomeAssistant,
    node_id: str,
    config_entry: ConfigEntry,
) -> None:
    """Set up IMU gesture discovery listener."""
    config = DiscoveryConfig(
        discovered_event=EVENT_IMU_GESTURE_DISCOVERED,
        entity_id_suffix=PLATFORM_TYPE_IMU_GESTURE,
        entity_class=ESPWeaverGestureSensor,
        entity_name=ENTITY_NAME_IMU_GESTURE,
    )
    create_discovery_listener(hass, node_id, config_entry, config)


class ESPWeaverGestureSensor(ESPWeaverBaseEntity, SensorEntity):
    """ESP-Weaver IMU gesture sensor entity.

    Represents an IMU gesture sensor from an ESP device, detecting gestures
    like tap, double-tap, shake, and tilt with auto-reset to idle state.
    """

    _attr_name = ENTITY_NAME_IMU_GESTURE
    _attr_translation_key = PLATFORM_TYPE_IMU_GESTURE

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the IMU gesture sensor."""
        super().__init__(
            coordinator,
            node_id,
            entity_key=PLATFORM_TYPE_IMU_GESTURE,
            device_name=device_name,
        )

        self._state = GestureState()
        self._reset_timer: asyncio.Task[None] | None = None
        self._gesture_events: dict[str, bool] = _gesture_processor.initialize_events()

        if initial_data:
            result = _gesture_processor.process_update(
                initial_data,
                self._gesture_events,
                self._state.gesture,
                self._state.display_duration,
                self._state.power,
                self._state.sensitivity,
                self._state.confidence,
            )
            self._apply_gesture_result(result)

    async def async_added_to_hass(self) -> None:
        """Register event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_IMU_GESTURE_UPDATE,
                self._handle_gesture_update,
            )
        )

        # Start auto-reset timer if initial state has an active gesture
        # This handles the case where entity is created with a gesture from initial_data
        if self._state.gesture and self._state.gesture != _gesture_processor.IDLE_STATE:
            self._reset_timer = self.hass.async_create_task(
                self._auto_reset_to_idle(),
                eager_start=True,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up resources when entity is removed from hass."""
        self._cancel_reset_timer()
        await super().async_will_remove_from_hass()

    def _cancel_reset_timer(self) -> None:
        """Cancel the existing reset timer if any."""
        if self._reset_timer is not None and not self._reset_timer.done():
            self._reset_timer.cancel()
            self._reset_timer = None

    async def _auto_reset_to_idle(self) -> None:
        """Automatically reset gesture to idle after display duration."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._state.display_duration)

            # Only perform reset if this task is still the registered timer
            if self._reset_timer is current_task:
                _LOGGER.debug(
                    "Auto-reset to idle after %.1fs (clearing orientation)",
                    self._state.display_duration,
                )
                self._state.gesture = _gesture_processor.IDLE_STATE
                # Only reset confidence if device reports it
                if self._state.confidence is not None:
                    self._state.confidence = 0
                self._gesture_events = _gesture_processor.reset_events(
                    self._gesture_events
                )
                self._state.orientation_x = None
                self._state.orientation_y = None
                self._state.orientation_z = None
                self._state.orientation_change = None
                self.async_write_ha_state()
        except asyncio.CancelledError:
            pass
        finally:
            # Only clear if we are still the registered timer
            if self._reset_timer is current_task:
                self._reset_timer = None

    @callback
    def _handle_gesture_update(self, event: Event) -> None:
        """Handle gesture update events."""
        if event.data.get(CONF_NODE_ID, "") != self._node_id:
            return

        sensor_data = event.data.get(KEY_SENSOR_DATA, {})

        # Process update using gesture processor
        result = _gesture_processor.process_update(
            sensor_data,
            self._gesture_events,
            self._state.gesture,
            self._state.display_duration,
            self._state.power,
            self._state.sensitivity,
            self._state.confidence,
        )

        # Apply orientation when a gesture was just triggered, OR when
        # we're still within the display window of a recent gesture
        # (reset timer is running) and new orientation data arrived.
        # This handles devices that send properties one at a time.
        within_gesture_window = (
            self._reset_timer is not None
            and not self._reset_timer.done()
            and bool(result.orientation)
        )
        self._apply_gesture_result(
            result,
            apply_orientation=result.gesture_triggered or within_gesture_window,
        )

        # Start auto-reset timer if a gesture was triggered
        if result.gesture_triggered:
            self._cancel_reset_timer()
            self._reset_timer = self.hass.async_create_task(
                self._auto_reset_to_idle(),
                eager_start=True,
            )

        self.async_write_ha_state()

    def _apply_gesture_result(
        self, result: GestureUpdateResult, *, apply_orientation: bool = True
    ) -> None:
        """Apply gesture processing result to state."""
        self._state.gesture = result.gesture
        self._state.confidence = result.confidence
        self._gesture_events = result.events
        self._state.display_duration = result.display_duration
        self._state.power = result.power
        self._state.sensitivity = result.sensitivity

        # Merge orientation only when the caller determines it is safe
        # (e.g. gesture just triggered or still within the display window)
        # to prevent stale reconnection data from overwriting idle-reset values.
        if apply_orientation and result.orientation:
            if KEY_ORIENTATION_X in result.orientation:
                self._state.orientation_x = result.orientation[KEY_ORIENTATION_X]
            if KEY_ORIENTATION_Y in result.orientation:
                self._state.orientation_y = result.orientation[KEY_ORIENTATION_Y]
            if KEY_ORIENTATION_Z in result.orientation:
                self._state.orientation_z = result.orientation[KEY_ORIENTATION_Z]
            if KEY_ORIENTATION_CHANGE in result.orientation:
                self._state.orientation_change = result.orientation[
                    KEY_ORIENTATION_CHANGE
                ]

            _LOGGER.debug(
                "Orientation updated: X=%s Y=%s Z=%s change=%s",
                self._state.orientation_x,
                self._state.orientation_y,
                self._state.orientation_z,
                self._state.orientation_change,
            )

    @property
    def native_value(self) -> str:
        """Return current gesture state."""
        # If power is reported and is False, show OFF state
        if self._state.power is False:
            return STATE_OFF
        return get_gesture_display_name(self._state.gesture)

    @property
    def icon(self) -> str:
        """Return icon based on current gesture."""
        # If power is reported and is False, show power-off icon
        if self._state.power is False:
            return "mdi:power-off"
        return get_gesture_icon(self._state.gesture)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes (only for supported features)."""
        # gesture is always required
        attrs: dict[str, Any] = {KEY_GESTURE: self._state.gesture}

        # Optional attributes - only add if device reports them
        if self._state.confidence is not None:
            attrs[KEY_CONFIDENCE] = self._state.confidence
        if self._state.power is not None:
            attrs[KEY_POWER] = self._state.power
        if self._state.sensitivity is not None:
            attrs[KEY_SENSITIVITY] = self._state.sensitivity

        # Only show the event attribute for the current gesture.
        # Hiding all "false" sibling events avoids cluttering the attributes
        # panel while preserving the full false→true→false lifecycle that
        # automations rely on for the active gesture.
        if self._gesture_events:
            current_event_attr = _gesture_processor.get_event_attr(self._state.gesture)
            if current_event_attr and current_event_attr in self._gesture_events:
                attrs[current_event_attr] = str(
                    self._gesture_events[current_event_attr]
                ).lower()

        # Only show orientation attributes with actual values
        if self._state.orientation_x is not None:
            attrs[ATTR_ORIENTATION_X] = self._state.orientation_x
        if self._state.orientation_y is not None:
            attrs[ATTR_ORIENTATION_Y] = self._state.orientation_y
        if self._state.orientation_z is not None:
            attrs[ATTR_ORIENTATION_Z] = self._state.orientation_z
        if self._state.orientation_change is not None:
            attrs[ATTR_ORIENTATION_CHANGE] = self._state.orientation_change

        if self._state.display_duration != DEFAULT_GESTURE_DISPLAY_DURATION:
            attrs[KEY_GESTURE_DISPLAY_DURATION] = self._state.display_duration

        return attrs
