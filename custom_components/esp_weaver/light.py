# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver light entity."""

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ENTITY_NAME_LIGHT, LIGHT_EFFECT_MODE_PREFIX, PLATFORM_LIGHT
from .entity import ESPWeaverBaseEntity
from .helpers.discovery import setup_platform_discovery, setup_single_entity_discovery
from .helpers.ha_types import ESPConfigEntry
from .helpers.light_control import setup_light_control_listener
from .iot.entity_states import LightState
from .iot.specs.events import (
    EVENT_LIGHT_DISCOVERED,
    EVENT_LIGHT_SET_PROPERTIES,
    EVENT_LIGHT_UPDATE,
)
from .iot.specs.keys import (
    CONF_NODE_ID,
    KEY_BRIGHTNESS,
    KEY_HS_COLOR,
    KEY_INTENSITY,
    KEY_IS_ON,
    KEY_LIGHT_DATA,
    KEY_LIGHT_MODE,
    KEY_PROPERTIES,
)
from .iot.specs.light_specs import ESP_PROP_LIGHT_MODE, LIGHT_EFFECT_MODES
from .iot.utils.light_utils import (
    build_light_turn_off_properties,
    build_light_turn_on_properties,
    parse_light_update,
)

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Coordinator handles all updates, no parallel update limit needed
PARALLEL_UPDATES: Final = 0

# Priority order for color mode fallback selection (most capable first)
_COLOR_MODE_PRIORITY: Final = (
    ColorMode.HS,
    ColorMode.BRIGHTNESS,
    ColorMode.ONOFF,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ESPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up light platform from config entry."""
    result = setup_platform_discovery(
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        platform_name=PLATFORM_LIGHT,
    )
    if not result:
        return

    # Setup light control listener (with proper cleanup)
    config_entry.async_on_unload(
        setup_light_control_listener(hass, result.node_id, result.coordinator.api)
    )

    # Entity factory for light discovery
    def create_light_entity(
        event_data: Mapping[str, Any],
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str | None,
    ) -> "ESPWeaverLight":
        """Create light entity from discovery event data."""
        return ESPWeaverLight(
            coordinator=coordinator,
            node_id=node_id,
            device_name=device_name,
            light_data=event_data.get(KEY_LIGHT_DATA, {}),
        )

    # Setup discovery listener using helper
    setup_single_entity_discovery(
        hass=hass,
        config_entry=config_entry,
        result=result,
        discovered_event=EVENT_LIGHT_DISCOVERED,
        platform_name=PLATFORM_LIGHT,
        entity_factory=create_light_entity,
    )


class ESPWeaverLight(ESPWeaverBaseEntity, LightEntity):
    """ESP-Weaver light entity.

    Represents a light from an ESP device. Supported features are dynamically
    determined based on device-reported parameters:
    - Power only → ONOFF mode
    - Power + Brightness → BRIGHTNESS mode
    - Power + Brightness + Hue + Saturation → HS mode
    """

    _attr_name = ENTITY_NAME_LIGHT
    _attr_translation_key = PLATFORM_LIGHT

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        device_name: str | None,
        light_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(
            coordinator, node_id, entity_key=PLATFORM_LIGHT, device_name=device_name
        )

        # Initialize light state
        self._state = LightState()

        if light_data:
            updates = parse_light_update(light_data, {})
            self._apply_state(updates)

        # Set supported color modes based on device capabilities
        self._attr_supported_color_modes = self._determine_supported_color_modes()

        # Set supported features based on device capabilities
        # Only enable EFFECT if device supports light_mode
        if self._state.light_mode is not None:
            self._attr_supported_features = LightEntityFeature.EFFECT
        else:
            self._attr_supported_features = LightEntityFeature(0)

    def _determine_supported_color_modes(self) -> set[ColorMode]:
        """Determine supported color modes based on device capabilities.

        Returns:
            Set of supported ColorMode values based on device-reported parameters.
        """
        if self._state.hs_color is not None:
            return {ColorMode.HS}

        if self._state.brightness is not None:
            return {ColorMode.BRIGHTNESS}

        return {ColorMode.ONOFF}

    def _apply_state(self, updates: dict[str, Any]) -> None:
        """Apply state updates to the light."""
        for key, value in updates.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)

    @property
    def is_on(self) -> bool:
        """Return True if light is on."""
        return self._state.is_on

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the current color mode based on device capabilities."""
        supported = self._attr_supported_color_modes or set()
        return next(
            (mode for mode in _COLOR_MODE_PRIORITY if mode in supported),
            None,
        )

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light, or None if off or not supported."""
        if self._state.brightness is None:
            return None
        return self._state.brightness if self.is_on else None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the HS color, or None if not supported."""
        return self._state.hs_color

    @property
    def effect_list(self) -> list[str] | None:
        """Return the list of supported effects, or None if not supported."""
        if self._state.light_mode is None:
            return None
        return list(LIGHT_EFFECT_MODES)

    @property
    def effect(self) -> str | None:
        """Return the current effect, or None if not supported."""
        if self._state.light_mode is None:
            return None
        return f"{LIGHT_EFFECT_MODE_PREFIX}{self._state.light_mode}"

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        """Return additional state attributes (only for supported features)."""
        attrs: dict[str, int | None] = {}
        if self._state.intensity is not None:
            attrs[KEY_INTENSITY] = self._state.intensity
        if self._state.light_mode is not None:
            attrs[KEY_LIGHT_MODE] = self._state.light_mode
        return attrs

    async def async_added_to_hass(self) -> None:
        """Register event listeners."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_LIGHT_UPDATE, self._handle_light_update)
        )

    def _get_current_state_dict(self) -> dict[str, Any]:
        """Convert current light state to dictionary for parsing.

        Returns:
            Dictionary mapping update keys to current state values.
        """
        return {
            KEY_IS_ON: self._state.is_on,
            KEY_BRIGHTNESS: self._state.brightness,
            KEY_HS_COLOR: self._state.hs_color,
            KEY_INTENSITY: self._state.intensity,
            KEY_LIGHT_MODE: self._state.light_mode,
        }

    @callback
    def _handle_light_update(self, event: Event) -> None:
        """Handle light state updates."""
        if event.data.get(CONF_NODE_ID, "") != self._node_id:
            return

        light_data = event.data.get(KEY_LIGHT_DATA, {})
        if not light_data:
            return

        updates = parse_light_update(light_data, self._get_current_state_dict())
        if not updates:
            return

        self._apply_state(updates)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on light."""
        try:
            properties = build_light_turn_on_properties(
                brightness=kwargs.get(ATTR_BRIGHTNESS),
                hs_color=kwargs.get(ATTR_HS_COLOR),
                effect=kwargs.get(ATTR_EFFECT),
            )
        except (ValueError, TypeError) as err:
            _LOGGER.error("Failed to build turn on properties: %s", err)
            raise HomeAssistantError(
                f"Failed to build light properties: {err}"
            ) from err

        # Update local state optimistically (only for supported features)
        self._state.is_on = True
        supported = self.supported_color_modes or set()
        if ATTR_BRIGHTNESS in kwargs and (
            ColorMode.BRIGHTNESS in supported or ColorMode.HS in supported
        ):
            self._state.brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_HS_COLOR in kwargs and self._state.hs_color is not None:
            self._state.hs_color = kwargs[ATTR_HS_COLOR]
        # Only update light_mode if device supports it
        if (
            ATTR_EFFECT in kwargs
            and ESP_PROP_LIGHT_MODE in properties
            and self._state.light_mode is not None
        ):
            self._state.light_mode = properties[ESP_PROP_LIGHT_MODE]

        # Send to device
        self.hass.bus.async_fire(
            EVENT_LIGHT_SET_PROPERTIES,
            {CONF_NODE_ID: self._node_id, KEY_PROPERTIES: properties},
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off light."""
        try:
            properties = build_light_turn_off_properties()
        except (ValueError, TypeError) as err:
            _LOGGER.error("Failed to build turn off properties: %s", err)
            raise HomeAssistantError(
                f"Failed to build turn off properties: {err}"
            ) from err

        self._state.is_on = False
        self.hass.bus.async_fire(
            EVENT_LIGHT_SET_PROPERTIES,
            {CONF_NODE_ID: self._node_id, KEY_PROPERTIES: properties},
        )
        self.async_write_ha_state()
