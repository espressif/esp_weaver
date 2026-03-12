# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver binary sensor entity."""

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_BINARY_SENSOR_TYPE,
    ENTITY_NAME_BINARY_SENSOR,
    PLATFORM_BINARY_SENSOR,
)
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import setup_platform_discovery, setup_single_entity_discovery
from .helpers.ha_types import ESPConfigEntry
from .iot.specs.events import EVENT_BINARY_SENSOR_DISCOVERED, EVENT_BINARY_SENSOR_UPDATE
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_DEBOUNCE_TIME,
    KEY_DEVICE_CLASS,
    KEY_NAME,
    KEY_PARAMS,
    KEY_REPORT_INTERVAL,
    KEY_STATE,
)
from .iot.utils.binary_sensor_utils import (
    get_binary_sensor_device_class,
    process_binary_sensor_update,
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
    """Set up binary sensor platform from config entry."""
    result = setup_platform_discovery(
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        platform_name=PLATFORM_BINARY_SENSOR,
    )
    if not result:
        return

    # Entity factory for binary sensor discovery
    def create_binary_sensor_entity(
        event_data: Mapping[str, Any],
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str | None,
    ) -> "ESPWeaverBinarySensor":
        """Create binary sensor entity from discovery event data."""
        return ESPWeaverBinarySensor(
            coordinator=coordinator,
            node_id=node_id,
            device_name=device_name,
            sensor_params=event_data.get(KEY_PARAMS, {}),
        )

    # Setup discovery listener using helper
    setup_single_entity_discovery(
        hass=hass,
        config_entry=config_entry,
        result=result,
        discovered_event=EVENT_BINARY_SENSOR_DISCOVERED,
        platform_name=PLATFORM_BINARY_SENSOR,
        entity_factory=create_binary_sensor_entity,
    )


class ESPWeaverBinarySensor(ESPWeaverBaseEntity, BinarySensorEntity):
    """ESP-Weaver binary sensor entity.

    Represents a binary sensor (on/off) from an ESP device, supporting
    automatic device class detection and state update handling.
    """

    _attr_translation_key = PLATFORM_BINARY_SENSOR
    _binary_sensor_type: str | None

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str | None,
        sensor_params: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(
            coordinator,
            node_id,
            entity_key=PLATFORM_BINARY_SENSOR,
            device_name=device_name,
        )
        params = sensor_params or {}

        self._attr_is_on = params.get(KEY_STATE)

        # Determine device class (get_binary_sensor_device_class handles None/empty)
        device_class_str = params.get(KEY_DEVICE_CLASS)
        device_class_normalized = get_binary_sensor_device_class(device_class_str)
        try:
            self._attr_device_class = BinarySensorDeviceClass(device_class_normalized)
            self._binary_sensor_type = device_class_normalized
        except ValueError:
            _LOGGER.warning(
                "Invalid binary sensor device class: %s", device_class_normalized
            )
            self._attr_device_class = None
            self._binary_sensor_type = None
        self._extra_attrs: dict[str, Any] = {}
        self._current_debounce_time: int | None = None
        self._current_report_interval: int | None = None

        self._attr_name = str(params.get(KEY_NAME) or ENTITY_NAME_BINARY_SENSOR)

    async def async_added_to_hass(self) -> None:
        """Register update listener when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_BINARY_SENSOR_UPDATE,
                self._handle_binary_sensor_update,
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs: dict[str, Any] = {ATTR_BINARY_SENSOR_TYPE: self._binary_sensor_type}

        if KEY_DEBOUNCE_TIME in self._extra_attrs:
            attrs[KEY_DEBOUNCE_TIME] = self._extra_attrs[KEY_DEBOUNCE_TIME]
        if KEY_REPORT_INTERVAL in self._extra_attrs:
            attrs[KEY_REPORT_INTERVAL] = self._extra_attrs[KEY_REPORT_INTERVAL]

        return attrs

    @callback
    def _handle_binary_sensor_update(self, event: Event) -> None:
        """Handle binary sensor update events."""
        event_node_id = event.data.get(CONF_NODE_ID, "")
        if event_node_id != self._node_id:
            return

        result = process_binary_sensor_update(
            event.data,
            self._attr_is_on,
            self._binary_sensor_type or "",
            current_debounce_time=self._current_debounce_time,
            current_report_interval=self._current_report_interval,
        )

        if result.state is not None:
            self._attr_is_on = result.state

        if result.device_class is not None:
            try:
                self._attr_device_class = BinarySensorDeviceClass(result.device_class)
                self._binary_sensor_type = result.device_class
            except ValueError:
                _LOGGER.warning(
                    "Invalid binary sensor device class in update: %s",
                    result.device_class,
                )
                # Clear both to match __init__ behavior for invalid device class
                self._attr_device_class = None
                self._binary_sensor_type = None

        if result.debounce_time is not None:
            self._current_debounce_time = result.debounce_time
            self._extra_attrs[KEY_DEBOUNCE_TIME] = result.debounce_time

        if result.report_interval is not None:
            self._current_report_interval = result.report_interval
            self._extra_attrs[KEY_REPORT_INTERVAL] = result.report_interval

        if result.has_changes:
            self.async_write_ha_state()
