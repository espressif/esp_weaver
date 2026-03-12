# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver interactive input sensor entity."""

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback

from .const import ENTITY_NAME_INTERACTIVE_INPUT
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import DiscoveryConfig, create_discovery_listener
from .iot.entity_states import InputState
from .iot.specs.events import (
    EVENT_INTERACTIVE_INPUT_DISCOVERED,
    EVENT_INTERACTIVE_INPUT_UPDATE,
)
from .iot.specs.input_specs import (
    INPUT_EVENT_NONE,
    INPUT_EVENT_OPTIONS,
    INPUT_TYPE_BUTTON,
)
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_INPUT_CONFIG,
    KEY_INPUT_DATA,
    KEY_INPUT_EVENTS,
    KEY_INPUT_MAPPING,
    KEY_INPUT_TYPE,
    KEY_INPUT_VALUE,
    KEY_LAST_EVENT,
    KEY_SENSITIVITY,
    PLATFORM_TYPE_INTERACTIVE_INPUT,
)
from .iot.utils.input_utils import get_input_icon, parse_input_update

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def setup_discovery_listener(
    hass: HomeAssistant,
    node_id: str,
    config_entry: ConfigEntry,
) -> None:
    """Set up interactive input discovery listener."""
    config = DiscoveryConfig(
        discovered_event=EVENT_INTERACTIVE_INPUT_DISCOVERED,
        entity_id_suffix=PLATFORM_TYPE_INTERACTIVE_INPUT,
        entity_class=ESPWeaverInputSensor,
        entity_name=ENTITY_NAME_INTERACTIVE_INPUT,
    )
    create_discovery_listener(hass, node_id, config_entry, config)


class ESPWeaverInputSensor(ESPWeaverBaseEntity, SensorEntity):
    """ESP-Weaver interactive input sensor entity.

    Represents an interactive input from an ESP device, such as buttons,
    encoders, or touch inputs with configurable mappings.
    """

    _attr_has_entity_name = True
    _attr_name = ENTITY_NAME_INTERACTIVE_INPUT
    _attr_translation_key = PLATFORM_TYPE_INTERACTIVE_INPUT
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: list[str] = list(INPUT_EVENT_OPTIONS)

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the interactive input entity."""
        super().__init__(
            coordinator,
            node_id,
            entity_key=PLATFORM_TYPE_INTERACTIVE_INPUT,
            device_name=device_name,
        )

        self._state = InputState(
            input_type=INPUT_TYPE_BUTTON,
            last_event=INPUT_EVENT_NONE,
            last_update=time.time(),
        )
        self._attr_native_value = INPUT_EVENT_NONE

        if initial_data:
            try:
                updates = parse_input_update(initial_data)
                self._apply_updates(updates)
            except (KeyError, ValueError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to parse initial input data for %s: %s",
                    device_name,
                    err,
                )

    async def async_added_to_hass(self) -> None:
        """Register event listeners when entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_INTERACTIVE_INPUT_UPDATE,
                self._handle_input_update,
            )
        )

    @property
    def icon(self) -> str:
        """Return the icon for the entity."""
        return get_input_icon(self._state.input_type, self._state.last_event)

    @callback
    def _handle_input_update(self, event: Event) -> None:
        """Handle interactive input update events."""
        if event.data.get(CONF_NODE_ID, "") != self._node_id:
            return

        input_data = event.data.get(KEY_INPUT_DATA, {})
        try:
            updates = parse_input_update(input_data)
            self._apply_updates(updates)
            self.async_write_ha_state()
        except (KeyError, ValueError, TypeError) as err:
            _LOGGER.error(
                "Failed to process input update for %s: %s",
                self.entity_id,
                err,
            )

    def _apply_updates(self, updates: dict[str, Any]) -> None:
        """Apply updates to entity state."""
        if not updates:
            return

        if KEY_INPUT_TYPE in updates:
            self._state.input_type = updates[KEY_INPUT_TYPE]
            self._state.last_update = time.time()

        if KEY_LAST_EVENT in updates:
            event_value = updates[KEY_LAST_EVENT]
            if event_value not in self._attr_options:
                _LOGGER.warning(
                    "Received unknown event '%s' for %s, ignoring",
                    event_value,
                    self.entity_id,
                )
            else:
                self._state.last_event = event_value
                self._attr_native_value = event_value
                self._state.last_update = time.time()

        if KEY_INPUT_VALUE in updates:
            self._state.input_value = updates[KEY_INPUT_VALUE]

        if KEY_SENSITIVITY in updates:
            self._state.sensitivity = updates[KEY_SENSITIVITY]

        if KEY_INPUT_CONFIG in updates:
            self._state.input_config = updates[KEY_INPUT_CONFIG]

        if KEY_INPUT_MAPPING in updates:
            self._state.input_mapping = updates[KEY_INPUT_MAPPING]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            KEY_INPUT_TYPE: self._state.input_type,
            KEY_INPUT_EVENTS: self._state.last_event,
        }

        if self._state.input_value is not None:
            attrs[KEY_INPUT_VALUE] = self._state.input_value

        if self._state.sensitivity is not None:
            attrs[KEY_SENSITIVITY] = self._state.sensitivity

        if self._state.input_config:
            attrs[KEY_INPUT_CONFIG] = self._state.input_config

        if self._state.input_mapping:
            attrs[KEY_INPUT_MAPPING] = self._state.input_mapping

        return attrs
