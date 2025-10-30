# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver battery sensor entity."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound

from .const import (
    ENTITY_NAME_BATTERY_ENERGY,
    SERVICE_CREATE,
    SERVICE_DISMISS,
    SERVICE_PERSISTENT_NOTIFICATION,
)
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import DiscoveryConfig, create_discovery_listener
from .iot.entity_states import BatteryState
from .iot.specs.battery_specs import (
    ALERT_LEVEL_NORMAL,
    BATTERY_ALERT_LEVELS,
    BATTERY_STATES,
)
from .iot.specs.events import (
    EVENT_BATTERY_ENERGY_DISCOVERED,
    EVENT_BATTERY_ENERGY_UPDATE,
)
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_ALERT_LEVEL,
    KEY_BATTERY_DATA,
    KEY_BATTERY_LEVEL,
    KEY_CHARGING_STATUS,
    KEY_NOTIFICATION_ID,
    KEY_TEMPERATURE,
    KEY_TITLE,
    KEY_VOLTAGE,
    PLATFORM_TYPE_BATTERY_ENERGY,
)
from .iot.utils.battery_utils import (
    build_battery_notification_data,
    get_battery_icon,
    get_battery_notification_ids_to_clear,
    parse_battery_update,
)

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def setup_discovery_listener(
    hass: HomeAssistant,
    node_id: str,
    config_entry: ConfigEntry,
) -> None:
    """Set up battery energy discovery listener."""
    config = DiscoveryConfig(
        discovered_event=EVENT_BATTERY_ENERGY_DISCOVERED,
        entity_id_suffix=PLATFORM_TYPE_BATTERY_ENERGY,
        entity_class=ESPWeaverBatterySensor,
        entity_name=ENTITY_NAME_BATTERY_ENERGY,
    )
    create_discovery_listener(hass, node_id, config_entry, config)


class ESPWeaverBatterySensor(ESPWeaverBaseEntity, SensorEntity):
    """ESP-Weaver battery sensor entity.

    Represents a battery sensor from an ESP device, providing battery level,
    voltage, temperature, charging status, and alert notifications.
    """

    _attr_name = ENTITY_NAME_BATTERY_ENERGY
    _attr_translation_key = PLATFORM_TYPE_BATTERY_ENERGY
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the battery sensor entity."""
        super().__init__(
            coordinator,
            node_id,
            entity_key=PLATFORM_TYPE_BATTERY_ENERGY,
            device_name=device_name,
        )

        self._state = BatteryState()
        self._last_alert_level = ALERT_LEVEL_NORMAL

        # Track pending notification tasks to cancel on entity removal
        self._notification_tasks: set[asyncio.Task[None]] = set()

        if initial_data:
            parsed_data = parse_battery_update(initial_data)
            self._apply_updates(parsed_data)

        self._attr_native_value = self._state.level

    async def async_added_to_hass(self) -> None:
        """Register event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_BATTERY_ENERGY_UPDATE,
                self._handle_battery_update,
            )
        )

        # Send initial alert if battery was critical/low at discovery time
        # (only if device reports alert_level)
        if (
            self._state.alert_level is not None
            and self._state.alert_level != ALERT_LEVEL_NORMAL
        ):
            self._send_alert_notification(self._state.alert_level)
            # Update _last_alert_level so _trigger_alert_notification doesn't re-send
            self._last_alert_level = self._state.alert_level

    async def async_will_remove_from_hass(self) -> None:
        """Cancel pending tasks when entity is removed."""
        tasks_to_cancel = [task for task in self._notification_tasks if not task.done()]
        for task in tasks_to_cancel:
            task.cancel()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._notification_tasks.clear()
        await super().async_will_remove_from_hass()

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return get_battery_icon(
            self._state.level,
            self._state.charging_status or "unknown",
            self._state.alert_level or "normal",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes (only for supported features)."""
        attrs: dict[str, Any] = {}

        if self._state.level is not None:
            attrs[KEY_BATTERY_LEVEL] = self._state.level

        if self._state.voltage is not None:
            attrs[KEY_VOLTAGE] = round(self._state.voltage, 2)

        if self._state.temperature is not None:
            attrs[KEY_TEMPERATURE] = round(self._state.temperature, 1)

        if self._state.charging_status is not None:
            attrs[KEY_CHARGING_STATUS] = BATTERY_STATES.get(
                self._state.charging_status, self._state.charging_status
            )

        if self._state.alert_level is not None:
            attrs[KEY_ALERT_LEVEL] = BATTERY_ALERT_LEVELS.get(
                self._state.alert_level, self._state.alert_level
            )

        return attrs

    @callback
    def _handle_battery_update(self, event: Event) -> None:
        """Handle battery energy update events."""
        if event.data.get(CONF_NODE_ID, "") != self._node_id:
            return

        parsed_data = parse_battery_update(event.data.get(KEY_BATTERY_DATA, {}))
        self._apply_updates(parsed_data)
        self.async_write_ha_state()

    def _apply_updates(self, parsed_data: dict[str, Any]) -> None:
        """Apply parsed update data to state."""
        if KEY_BATTERY_LEVEL in parsed_data:
            self._state.level = parsed_data[KEY_BATTERY_LEVEL]
            self._attr_native_value = self._state.level

        if KEY_VOLTAGE in parsed_data:
            self._state.voltage = parsed_data[KEY_VOLTAGE]

        if KEY_TEMPERATURE in parsed_data:
            self._state.temperature = parsed_data[KEY_TEMPERATURE]

        if KEY_CHARGING_STATUS in parsed_data:
            self._state.charging_status = parsed_data[KEY_CHARGING_STATUS]

        if KEY_ALERT_LEVEL in parsed_data:
            self._state.alert_level = parsed_data[KEY_ALERT_LEVEL]
            self._trigger_alert_notification()

    @callback
    def _trigger_alert_notification(self) -> None:
        """Trigger alert notification if level changed."""
        current_alert = self._state.alert_level
        if current_alert is None or current_alert == self._last_alert_level:
            return

        old_level = self._last_alert_level
        self._last_alert_level = current_alert

        # Skip notification if hass is not available yet (during __init__)
        # Note: hass may be None before async_added_to_hass is called
        if not hasattr(self, "hass") or self.hass is None:
            # Reset to allow notification when hass becomes available
            self._last_alert_level = old_level
            return

        self._send_alert_notification(current_alert)

    @callback
    def _send_alert_notification(self, alert_level: str) -> None:
        """Send alert notification for the given alert level."""
        notification_data = build_battery_notification_data(
            self._device_name,
            self._node_id,
            alert_level,
            self._state.level,
        )

        if notification_data:
            self._create_tracked_notification_task(SERVICE_CREATE, notification_data)
            _LOGGER.warning(
                "Battery alert - %s: %s%%",
                notification_data.get(KEY_TITLE, "Unknown"),
                self._state.level,
            )
        else:
            # Clear notifications when battery returns to normal
            for nid in get_battery_notification_ids_to_clear(self._node_id):
                self._create_tracked_notification_task(
                    SERVICE_DISMISS, {KEY_NOTIFICATION_ID: nid}
                )

    def _create_tracked_notification_task(
        self,
        service: str,
        service_data: dict[str, Any],
    ) -> None:
        """Create a notification task and track it for cleanup."""
        task = self.hass.async_create_task(
            self._safe_notification_call(service, service_data)
        )
        self._notification_tasks.add(task)
        task.add_done_callback(self._notification_tasks.discard)

    async def _safe_notification_call(
        self,
        service: str,
        service_data: dict[str, Any],
    ) -> None:
        """Call notification service with exception handling."""
        try:
            await self.hass.services.async_call(
                SERVICE_PERSISTENT_NOTIFICATION,
                service,
                service_data,
            )
        except ServiceNotFound:
            _LOGGER.debug(
                "Notification service %s not found",
                service,
            )
        except HomeAssistantError as err:
            _LOGGER.debug(
                "Failed to call notification service %s: %s",
                service,
                err,
            )
