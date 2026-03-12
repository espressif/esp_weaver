# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver number entity for threshold controls."""

import asyncio
from collections.abc import Callable, Sequence
import datetime
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfPressure
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_LAST_UPDATED, CACHE_NUMBERS, PLATFORM_NUMBER
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import setup_platform_discovery
from .helpers.ha_types import ESPConfigEntry
from .helpers.threshold_manager import ThresholdManager
from .helpers.utils import get_number_device_class_and_unit
from .iot.specs.events import (
    DOMAIN,
    EVENT_SENSOR_DISCOVERED,
    EVENT_THRESHOLD_DATA_RECEIVED,
    EVENT_THRESHOLD_UPDATE_TO_DEVICE,
)
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_ENTITY_ID,
    KEY_PARAM_NAME,
    KEY_SENSOR_TYPE,
    KEY_SOURCE,
    KEY_THRESHOLD_TYPE,
    KEY_THRESHOLD_VALUES,
    KEY_VALUE,
    SOURCE_NUMBER_ENTITY,
    THRESHOLD_TYPE_MAX,
    THRESHOLD_TYPE_MIN,
)
from .iot.specs.sensor_specs import (
    get_base_sensor_type,
    get_device_param_prefix,
    get_sensor_display_name,
    get_threshold_icon,
)
from .iot.utils.number_utils import (
    format_range_for_log,
    format_value_for_log,
    get_device_threshold_param_name,
    get_number_range_config,
    get_sensor_entity_info,
    hpa_to_inhg,
    inhg_to_hpa,
    is_imperial_unit_system,
)

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
    """Set up number platform from config entry."""
    result = setup_platform_discovery(
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        platform_name=PLATFORM_NUMBER,
    )
    if not result:
        return

    api = result.coordinator.api

    # Setup threshold manager for this config entry (pass API to avoid repeated lookups)
    threshold_manager = ThresholdManager(hass, DOMAIN, api=api)
    unsubscribe_callbacks = threshold_manager.setup_listeners(result.node_id)

    # Register unsubscribe callbacks for cleanup
    for unsub in unsubscribe_callbacks:
        config_entry.async_on_unload(unsub)

    threshold_manager.replay_discovered_sensors(
        result.node_id,
        get_sensor_entity_info,
        coordinator=result.coordinator,
    )

    @callback
    def handle_sensor_discovered(event: Event) -> None:
        """Handle sensor discovery to create threshold entities."""
        task = hass.async_create_task(
            _handle_sensor_discovered(
                event=event,
                node_id=result.node_id,
                api=api,
                discovered_entities=result.discovered_entities,
                async_add_entities=async_add_entities,
                coordinator=result.coordinator,
                threshold_manager=threshold_manager,
            )
        )

        def log_task_exception(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            if exc := t.exception():
                _LOGGER.exception("Error handling sensor discovery", exc_info=exc)

        task.add_done_callback(log_task_exception)

    # Listen for sensor discovery events (with proper cleanup)
    config_entry.async_on_unload(
        hass.bus.async_listen(EVENT_SENSOR_DISCOVERED, handle_sensor_discovered)
    )


async def _handle_sensor_discovered(
    event: Event,
    node_id: str,
    api: Any,
    discovered_entities: dict[str, Any],
    async_add_entities: Callable[[Sequence[NumberEntity]], None],
    coordinator: "ESPDataUpdateCoordinator",
    threshold_manager: ThresholdManager,
) -> None:
    """Handle sensor discovery event to create threshold entities."""
    event_node_id, sensor_type, device_name = (
        threshold_manager.extract_discovery_event_data(event.data)
    )

    if not event_node_id:
        return

    if event_node_id != node_id:
        return

    if not sensor_type or not api:
        return

    threshold_values = event.data.get(KEY_THRESHOLD_VALUES)
    if not isinstance(threshold_values, dict):
        threshold_values = {}

    new_entities: list[ESPWeaverThresholdNumber] = []

    for threshold_type in [THRESHOLD_TYPE_MIN, THRESHOLD_TYPE_MAX]:
        entity_key = f"{event_node_id}_{sensor_type}_{threshold_type}_threshold"

        if entity_key in discovered_entities:
            continue

        threshold_entity = ESPWeaverThresholdNumber(
            coordinator=coordinator,
            node_id=event_node_id,
            device_name=device_name,
            sensor_type=sensor_type,
            threshold_type=threshold_type,
            initial_value=threshold_values.get(threshold_type),
        )

        discovered_entities[entity_key] = threshold_entity
        new_entities.append(threshold_entity)

    if new_entities:
        async_add_entities(new_entities)


class ESPWeaverThresholdNumber(ESPWeaverBaseEntity, NumberEntity):
    """ESP-Weaver threshold number entity.

    Represents a threshold control for sensor values, allowing users to set
    minimum and maximum threshold values that trigger alerts.
    """

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str | None,
        sensor_type: str,
        threshold_type: str,
        initial_value: float | None = None,
    ) -> None:
        """Initialize the threshold number entity."""
        self._sensor_type = sensor_type.lower()
        self._threshold_type = threshold_type
        entity_key = f"{self._sensor_type}_{threshold_type}_threshold"

        super().__init__(
            coordinator, node_id, entity_key=entity_key, device_name=device_name
        )

        # Use translation_key for localized names
        self._attr_translation_key = f"{self._sensor_type}_{threshold_type}_threshold"

        # Generate display name for entities without translation
        sensor_display_name = get_sensor_display_name(self._sensor_type)
        threshold_display_name = (
            "Min" if threshold_type == THRESHOLD_TYPE_MIN else "Max"
        )
        self._attr_name = f"{sensor_display_name} {threshold_display_name} Threshold"

        # Set device class and unit for HA unit handling
        device_class, unit = get_number_device_class_and_unit(self._sensor_type)
        base_type = get_base_sensor_type(self._sensor_type)

        # Temperature, humidity: HA auto-converts with device_class
        if base_type in ("temperature", "humidity"):
            self._attr_device_class = device_class

        # Pressure: HA NumberEntity does NOT auto-convert ATMOSPHERIC_PRESSURE units
        # (unlike SensorEntity), so we must handle conversion manually
        self._use_inhg = False
        if base_type == "pressure":
            self._attr_device_class = device_class
            self._use_inhg = is_imperial_unit_system(coordinator.hass)
            if self._use_inhg:
                unit = UnitOfPressure.INHG

        self._attr_native_unit_of_measurement = unit

        # Get range configuration (in hPa for pressure)
        range_config = get_number_range_config(self._sensor_type, self._threshold_type)
        min_val = range_config[THRESHOLD_TYPE_MIN]
        max_val = range_config[THRESHOLD_TYPE_MAX]
        step_val = range_config["step"]
        default_val = range_config["default"]

        # Convert pressure values from hPa to inHg for imperial users
        if self._use_inhg:
            min_val = hpa_to_inhg(min_val)
            max_val = hpa_to_inhg(max_val)
            step_val = round(hpa_to_inhg(step_val), 2)
            default_val = hpa_to_inhg(default_val)
            if initial_value is not None:
                initial_value = hpa_to_inhg(initial_value)

        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step_val
        self._attr_native_value = (
            initial_value if initial_value is not None else default_val
        )
        self._attr_icon = get_threshold_icon(self._sensor_type, self._threshold_type)
        self._last_device_sync: datetime.datetime | None = None

        _LOGGER.debug(
            "%s %s threshold: %s",
            self._sensor_type,
            self._threshold_type,
            format_range_for_log(
                coordinator.hass,
                range_config[THRESHOLD_TYPE_MIN],
                range_config[THRESHOLD_TYPE_MAX],
                range_config["step"],
                range_config["default"],
                self._sensor_type,
            ),
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the value and sync to device.

        Validates that min threshold < max threshold before applying.
        Updates HA state only after successful sync to device.

        Raises:
            ServiceValidationError: If the value would violate min < max constraint.
        """
        # Validate min < max constraint
        paired_value = self._get_paired_threshold_value()
        if paired_value is not None:
            if self._threshold_type == THRESHOLD_TYPE_MIN and value >= paired_value:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="min_threshold_too_high",
                    translation_placeholders={
                        "value": str(value),
                        "max_value": str(paired_value),
                    },
                )
            if self._threshold_type == THRESHOLD_TYPE_MAX and value <= paired_value:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="max_threshold_too_low",
                    translation_placeholders={
                        "value": str(value),
                        "min_value": str(paired_value),
                    },
                )

        # Store previous value in case sync fails
        previous_value = self._attr_native_value
        self._attr_native_value = value

        try:
            await self._sync_to_device()
        except (OSError, TimeoutError, ConnectionError, RuntimeError):
            # Revert to previous value on sync failure
            self._attr_native_value = previous_value
            self.async_write_ha_state()
            raise

        self.async_write_ha_state()

    def _get_paired_threshold_value(self) -> float | None:
        """Get the value of the paired threshold entity (min gets max, max gets min).

        Returns:
            The paired threshold value, or None if not found.
        """
        # Determine the paired threshold type
        paired_type = (
            THRESHOLD_TYPE_MAX
            if self._threshold_type == THRESHOLD_TYPE_MIN
            else THRESHOLD_TYPE_MIN
        )

        # Look up the paired entity in coordinator's discovered entities
        # Use cache key CACHE_NUMBERS, not platform name PLATFORM_NUMBER
        paired_key = f"{self._node_id}_{self._sensor_type}_{paired_type}_threshold"
        discovered = self.coordinator.discovered_entities.get(CACHE_NUMBERS, {})
        paired_entity = discovered.get(paired_key)

        if paired_entity is not None:
            value: float | None = paired_entity.native_value
            return value

        return None

    async def _sync_to_device(self) -> None:
        """Sync current threshold value to device.

        Device always expects values in native units (hPa for pressure).
        If using imperial display (inHg), convert back to hPa before sending.
        """
        param_name = get_device_threshold_param_name(
            self._sensor_type, self._threshold_type
        )

        device_value = self._attr_native_value

        # Convert inHg back to hPa for device
        if self._use_inhg and device_value is not None:
            device_value = inhg_to_hpa(device_value)

        self.hass.bus.async_fire(
            EVENT_THRESHOLD_UPDATE_TO_DEVICE,
            {
                CONF_NODE_ID: self._node_id,
                KEY_PARAM_NAME: param_name,
                KEY_VALUE: device_value,
                KEY_SENSOR_TYPE: self._sensor_type,
                KEY_THRESHOLD_TYPE: self._threshold_type,
                KEY_ENTITY_ID: self.entity_id,
                KEY_SOURCE: SOURCE_NUMBER_ENTITY,
            },
        )

        self._last_device_sync = datetime.datetime.now(datetime.UTC)

    async def async_set_device_value(self, value: float) -> None:
        """Set value from device without triggering sync back.

        Args:
            value: Value in native units from device (hPa for pressure).
        """
        _LOGGER.debug(
            "Received %s from device: %s",
            self._sensor_type,
            format_value_for_log(self.hass, value, self._sensor_type),
        )

        # Convert hPa to inHg for imperial users
        if self._use_inhg:
            value = hpa_to_inhg(value)

        await self._set_device_value_no_convert(value)

    async def _set_device_value_no_convert(self, value: float) -> None:
        """Set value that's already in the correct display unit.

        Args:
            value: Value already converted to display units (inHg if imperial).
        """
        self._attr_native_value = value
        self._last_device_sync = datetime.datetime.now(datetime.UTC)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Handle entity added to Home Assistant."""
        await super().async_added_to_hass()
        self._setup_threshold_listeners()

    def _setup_threshold_listeners(self) -> None:
        """Set up listeners for threshold updates from device."""

        @callback
        def handle_device_threshold_update(event: Event) -> None:
            """Handle threshold update from device."""
            if event.data.get(CONF_NODE_ID, "") != self._node_id:
                return

            # Use the same prefix function used for sending to device
            device_prefix = get_device_param_prefix(self._sensor_type)
            expected_param = f"{device_prefix}_{self._threshold_type}_threshold"

            if event.data.get(KEY_PARAM_NAME, "") == expected_param:
                raw_value = event.data.get(KEY_VALUE)
                if raw_value is None:
                    _LOGGER.debug(
                        "No value in threshold update event for %s",
                        self.entity_id,
                    )
                    return
                try:
                    native_value = float(raw_value)

                    # Convert hPa to inHg for imperial users BEFORE range check
                    # Device always reports pressure in hPa, but UI range is in inHg
                    if self._use_inhg:
                        native_value = hpa_to_inhg(native_value)

                    if not (
                        self._attr_native_min_value
                        <= native_value
                        <= self._attr_native_max_value
                    ):
                        _LOGGER.warning(
                            "Threshold value %s out of range [%s, %s] for %s",
                            native_value,
                            self._attr_native_min_value,
                            self._attr_native_max_value,
                            self.entity_id,
                        )
                        return

                    # Pass already-converted value to avoid double conversion
                    task = self.hass.async_create_task(
                        self._set_device_value_no_convert(native_value)
                    )

                    def log_threshold_error(t: asyncio.Task) -> None:
                        if t.cancelled():
                            return
                        if exc := t.exception():
                            _LOGGER.exception(
                                "Error updating threshold value", exc_info=exc
                            )

                    task.add_done_callback(log_threshold_error)
                except (ValueError, TypeError) as err:
                    _LOGGER.error(
                        "Invalid threshold value received: %s",
                        event.data.get(KEY_VALUE),
                        exc_info=err,
                    )

        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_THRESHOLD_DATA_RECEIVED, handle_device_threshold_update
            )
        )

    @property
    def sensor_type(self) -> str:
        """Return the sensor type."""
        return self._sensor_type

    @property
    def threshold_type(self) -> str:
        """Return the threshold type."""
        return self._threshold_type

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self._last_device_sync is not None:
            attrs[ATTR_LAST_UPDATED] = self._last_device_sync.isoformat()
        return attrs
