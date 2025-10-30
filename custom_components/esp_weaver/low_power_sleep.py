# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver low power sleep sensor entity."""

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback

from .const import ENTITY_NAME_LOW_POWER_SLEEP
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import DiscoveryConfig, create_discovery_listener
from .iot.entity_states import SleepState
from .iot.specs.events import (
    EVENT_LOW_POWER_SLEEP_DISCOVERED,
    EVENT_LOW_POWER_SLEEP_UPDATE,
)
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_SLEEP_DATA,
    KEY_SLEEP_DURATION,
    KEY_SLEEP_STATE,
    KEY_WAKE_COUNT,
    KEY_WAKE_REASON,
    KEY_WAKE_WINDOW_STATUS,
    PLATFORM_TYPE_LOW_POWER_SLEEP,
)
from .iot.specs.sleep_specs import SLEEP_STATE_AWAKE, SLEEP_STATE_OPTIONS
from .iot.utils.sleep_utils import get_sleep_icon

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def setup_discovery_listener(
    hass: HomeAssistant,
    node_id: str,
    config_entry: ConfigEntry,
) -> None:
    """Set up low power sleep discovery listener."""
    config = DiscoveryConfig(
        discovered_event=EVENT_LOW_POWER_SLEEP_DISCOVERED,
        entity_id_suffix=PLATFORM_TYPE_LOW_POWER_SLEEP,
        entity_class=ESPWeaverSleepSensor,
        entity_name=ENTITY_NAME_LOW_POWER_SLEEP,
    )
    create_discovery_listener(hass, node_id, config_entry, config)


class ESPWeaverSleepSensor(ESPWeaverBaseEntity, SensorEntity):
    """ESP-Weaver low power sleep sensor entity.

    Represents the sleep state of an ESP device, tracking wake reasons,
    sleep duration, and wake count for power management monitoring.
    """

    _attr_has_entity_name = True
    _attr_name = ENTITY_NAME_LOW_POWER_SLEEP
    _attr_translation_key = PLATFORM_TYPE_LOW_POWER_SLEEP
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: list[str] = list(SLEEP_STATE_OPTIONS)

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the low power sleep entity."""
        super().__init__(
            coordinator,
            node_id,
            entity_key=PLATFORM_TYPE_LOW_POWER_SLEEP,
            device_name=device_name,
        )

        self._state = SleepState(
            sleep_state=SLEEP_STATE_AWAKE,
            wake_reason=STATE_UNKNOWN,
            last_wake_time=None,
        )

        if initial_data:
            self._apply_sleep_data(initial_data)

        self._attr_native_value = self._state.sleep_state

    async def async_added_to_hass(self) -> None:
        """Register event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_LOW_POWER_SLEEP_UPDATE,
                self._handle_sleep_update,
            )
        )

    @property
    def icon(self) -> str:
        """Return the icon based on current sleep state."""
        return get_sleep_icon(self._state.sleep_state)

    def _apply_sleep_data(self, sleep_data: dict[str, Any]) -> None:
        """Apply sleep data to state."""
        if KEY_SLEEP_STATE in sleep_data:
            new_state = sleep_data[KEY_SLEEP_STATE]
            # Validate against allowed options
            if new_state in SLEEP_STATE_OPTIONS:
                self._state.sleep_state = new_state
            else:
                _LOGGER.debug(
                    "Invalid sleep_state value '%s', keeping current state", new_state
                )

        if KEY_WAKE_REASON in sleep_data:
            self._state.wake_reason = sleep_data[KEY_WAKE_REASON]

        if KEY_WAKE_WINDOW_STATUS in sleep_data:
            self._state.wake_window_status = sleep_data[KEY_WAKE_WINDOW_STATUS]

        if KEY_SLEEP_DURATION in sleep_data:
            try:
                duration = int(sleep_data[KEY_SLEEP_DURATION])
                if duration < 0:
                    _LOGGER.debug("Invalid sleep_duration (negative): %s", duration)
                else:
                    self._state.sleep_duration = duration
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Invalid sleep_duration value: %s", sleep_data[KEY_SLEEP_DURATION]
                )

        if KEY_WAKE_COUNT in sleep_data:
            try:
                new_count = int(sleep_data[KEY_WAKE_COUNT])
                if new_count < 0:
                    _LOGGER.debug("Invalid wake_count (negative): %s", new_count)
                else:
                    # Update last_wake_time if count increased (or first report)
                    if (
                        self._state.wake_count is None
                        or new_count > self._state.wake_count
                    ):
                        self._state.last_wake_time = time.time()
                    self._state.wake_count = new_count
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Invalid wake_count value: %s", sleep_data[KEY_WAKE_COUNT]
                )

    @callback
    def _handle_sleep_update(self, event: Event) -> None:
        """Handle low power sleep state update event."""
        if event.data.get(CONF_NODE_ID, "") != self._node_id:
            return

        sleep_data = event.data.get(KEY_SLEEP_DATA, {})
        if not sleep_data:
            return

        self._apply_sleep_data(sleep_data)
        self._attr_native_value = self._state.sleep_state
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes (only for supported features)."""
        attrs: dict[str, Any] = {KEY_WAKE_REASON: self._state.wake_reason}

        if self._state.wake_window_status is not None:
            attrs[KEY_WAKE_WINDOW_STATUS] = self._state.wake_window_status

        if self._state.sleep_duration is not None:
            attrs[KEY_SLEEP_DURATION] = self._state.sleep_duration

        # Only include wake_count if device reports it
        if self._state.wake_count is not None:
            attrs[KEY_WAKE_COUNT] = self._state.wake_count

        return attrs
