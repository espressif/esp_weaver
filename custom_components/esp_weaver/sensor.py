# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver sensor entity."""

import asyncio
from collections.abc import Mapping
import contextlib
import datetime
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ATTR_LAST_UPDATED, PLATFORM_SENSOR
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import setup_platform_discovery
from .helpers.ha_types import ESPConfigEntry
from .helpers.sensor_alert import SensorAlertService
from .helpers.utils import get_sensor_mapping
from .iot.specs.device_specs import DEFAULT_DEVICE_NAME_PREFIX
from .iot.specs.events import DOMAIN, EVENT_SENSOR_DISCOVERED, EVENT_SENSOR_UPDATE
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_DEVICE_CLASS,
    KEY_DEVICE_INFO,
    KEY_DEVICE_NAME,
    KEY_INITIAL_VALUE,
    KEY_NAME,
    KEY_SENSOR_TYPE,
    KEY_TYPE,
    KEY_UNIT_OF_MEASUREMENT,
    KEY_VALUE,
)
from .iot.specs.sensor_specs import (
    get_sensor_display_name,
    get_sensor_display_precision,
    get_sensor_unit,
    is_threshold_pattern,
)
from .iot.utils.sensor_utils import get_sensor_threshold_config

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Coordinator handles all updates, no parallel update limit needed
PARALLEL_UPDATES: Final = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ESPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform from config entry."""
    result = setup_platform_discovery(
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        platform_name=PLATFORM_SENSOR,
    )
    if not result:
        return

    @callback
    def handle_sensor_discovered(event: Event) -> None:
        """Handle sensor discovery events."""
        if event.data.get(CONF_NODE_ID, "") != result.node_id:
            return

        sensor_type = event.data.get(KEY_SENSOR_TYPE, "")
        if is_threshold_pattern(sensor_type):
            return

        entity_key = f"{result.node_id}_{sensor_type}"
        if entity_key in result.discovered_entities:
            return

        sensor_entity = _create_sensor_entity(
            event.data,
            result.coordinator,
            result.node_id,
        )

        result.discovered_entities[entity_key] = sensor_entity
        async_add_entities([sensor_entity])

    config_entry.async_on_unload(
        hass.bus.async_listen(EVENT_SENSOR_DISCOVERED, handle_sensor_discovered)
    )


def _create_sensor_entity(
    event_data: Mapping[str, Any],
    coordinator: "ESPDataUpdateCoordinator",
    node_id: str,
) -> "ESPWeaverSensor":
    """Create a sensor entity from event data."""
    sensor_type = event_data.get(KEY_SENSOR_TYPE, "")
    device_info = event_data.get(KEY_DEVICE_INFO, {})
    initial_value = event_data.get(KEY_INITIAL_VALUE)
    unit = event_data.get(KEY_UNIT_OF_MEASUREMENT, "")
    device_class = event_data.get(KEY_DEVICE_CLASS)

    # Get sensor mapping: (device_class, state_class, unit)
    sensor_mapping = get_sensor_mapping()
    mapping = sensor_mapping.get(
        sensor_type, (None, SensorStateClass.MEASUREMENT, None)
    )

    # Determine device class (from event data or mapping)
    raw_device_class = device_class if device_class else mapping[0]
    if isinstance(raw_device_class, str):
        try:
            final_device_class: SensorDeviceClass | None = SensorDeviceClass(
                raw_device_class
            )
        except ValueError:
            final_device_class = None
    else:
        final_device_class = raw_device_class
    final_state_class = mapping[1]
    # Priority: sensor specs > mapping > event data
    final_unit = get_sensor_unit(sensor_type)
    if not final_unit:
        final_unit = mapping[2] if mapping[2] else unit

    device_name = device_info.get(KEY_NAME)
    if not device_name:
        device_name = event_data.get(
            KEY_DEVICE_NAME, f"{DEFAULT_DEVICE_NAME_PREFIX}{node_id}"
        )

    return ESPWeaverSensor(
        coordinator=coordinator,
        node_id=node_id,
        device_name=device_name,
        sensor_type=sensor_type,
        unit=final_unit,
        device_class=final_device_class,
        state_class=final_state_class,
        initial_value=initial_value,
    )


class ESPWeaverSensor(ESPWeaverBaseEntity, SensorEntity):
    """ESP-Weaver sensor entity.

    Represents a sensor from an ESP device, supporting various sensor types
    with automatic unit, device class detection, and threshold monitoring.
    """

    _attr_force_update = False

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str,
        sensor_type: str,
        unit: str,
        device_class: SensorDeviceClass | None,
        state_class: SensorStateClass | None,
        initial_value: Any = None,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(
            coordinator, node_id, entity_key=sensor_type, device_name=device_name
        )
        self._sensor_type = sensor_type
        self._last_update_time: datetime.datetime | None = None

        # Alert service for threshold violations (lazily accessed via hass)
        self._alert_service: SensorAlertService | None = None

        # Track pending threshold check task to cancel on entity removal
        self._threshold_check_task: asyncio.Task[None] | None = None

        # Use translation_key for localized names (falls back to _attr_name)
        self._attr_translation_key = sensor_type
        self._attr_name = get_sensor_display_name(sensor_type)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

        display_precision = get_sensor_display_precision(sensor_type)
        if display_precision is not None:
            self._attr_suggested_display_precision = display_precision

        if initial_value is not None:
            try:
                self._attr_native_value = float(initial_value)
                self._last_update_time = dt_util.utcnow()
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid initial value for %s: %s", sensor_type, initial_value
                )

    @property
    def sensor_type(self) -> str:
        """Return sensor type."""
        return self._sensor_type

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}

        if self._last_update_time is not None:
            attrs[ATTR_LAST_UPDATED] = self._last_update_time.isoformat()

        threshold_config = get_sensor_threshold_config(self._sensor_type)
        if threshold_config is not None:
            attrs.update(threshold_config)

        return attrs

    async def async_added_to_hass(self) -> None:
        """Register event listeners."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_SENSOR_UPDATE, self._handle_sensor_update)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel pending tasks when entity is removed."""
        if (
            self._threshold_check_task is not None
            and not self._threshold_check_task.done()
        ):
            self._threshold_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._threshold_check_task
            self._threshold_check_task = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_sensor_update(self, event: Event) -> None:
        """Handle sensor update event."""
        event_node_id = event.data.get(CONF_NODE_ID, "")
        if event_node_id != self._node_id:
            return

        event_type = event.data.get(KEY_TYPE, "")
        if event_type != self._sensor_type:
            return

        new_value_data = event.data.get(KEY_VALUE)
        if new_value_data is None:
            return

        try:
            new_value = float(new_value_data)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Invalid sensor value for %s: %s", self._sensor_type, new_value_data
            )
            return

        # Capture old value before updating
        old_value = self._attr_native_value

        self._last_update_time = dt_util.utcnow()
        self._attr_native_value = new_value
        self.async_write_ha_state()

        # Check threshold violations after state update
        if old_value != new_value:
            old_float = (
                float(old_value) if isinstance(old_value, (int, float)) else None
            )
            # Cancel any pending threshold check before scheduling new one
            if (
                self._threshold_check_task is not None
                and not self._threshold_check_task.done()
            ):
                self._threshold_check_task.cancel()
            # Schedule threshold check as background task
            task = self.hass.async_create_task(
                self._check_threshold_violations(new_value, old_float),
                name=f"esp_weaver_threshold_check_{self._sensor_type}",
            )
            self._threshold_check_task = task

    async def _check_threshold_violations(
        self,
        current_value: float,
        old_value: float | None,
    ) -> None:
        """Check threshold violations using SensorAlertService."""
        try:
            # Lazily initialize alert service
            if self._alert_service is None:
                self._alert_service = SensorAlertService(self.hass, DOMAIN)

            await self._alert_service.check_and_handle_violations(
                node_id=self._node_id,
                device_name=self._device_name,
                sensor_type=self._sensor_type,
                current_value=current_value,
                old_value=old_value,
            )
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            # Task was cancelled (entity being removed), propagate
            raise
        except (OSError, TimeoutError, RuntimeError) as err:
            _LOGGER.error(
                "Error checking threshold violations for %s: %s",
                self._sensor_type,
                err,
            )
        finally:
            # Clear task ref only if it's still this task (avoid clearing newer)
            current_task = asyncio.current_task()
            if self._threshold_check_task is current_task:
                self._threshold_check_task = None
