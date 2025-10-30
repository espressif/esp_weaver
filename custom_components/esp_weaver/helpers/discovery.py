# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Discovery utilities for ESP-Weaver entities."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import (
    CACHE_BINARY_SENSORS,
    CACHE_LIGHTS,
    CACHE_NUMBERS,
    CACHE_SENSORS,
    KEY_PASSWORD,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_LIGHT,
    PLATFORM_NUMBER,
    PLATFORM_SENSOR,
)
from ..iot.specs.events import DOMAIN
from ..iot.specs.keys import CONF_NODE_ID, KEY_DEVICE_NAME, KEY_INITIAL_DATA

if TYPE_CHECKING:
    from ..coordinator import ESPDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class DiscoveryConfig:
    """Configuration for entity discovery listener.

    Attributes:
        discovered_event: The full discovery event name
            (e.g., "esp_weaver_battery_energy_discovered")
        entity_id_suffix: The entity ID suffix (e.g., "battery_energy")
        entity_class: The entity class to instantiate
        platform: The platform name for callback lookup (default: "sensor")
        entity_name: Optional display name for logging
        extra_entity_kwargs: Optional extra kwargs builder function
    """

    discovered_event: str
    entity_id_suffix: str
    entity_class: type
    platform: str = PLATFORM_SENSOR
    entity_name: str = ""
    extra_entity_kwargs: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        """Set default entity name if not provided."""
        if not self.entity_name:
            self.entity_name = self.entity_id_suffix.replace("_", " ").title()


def create_discovery_listener(
    hass: HomeAssistant,
    node_id: str,
    config_entry: ConfigEntry,
    config: DiscoveryConfig,
) -> None:
    """Create and register a generic entity discovery listener."""
    device_name = config_entry.title

    @callback
    def handle_entity_discovered(event: Event) -> None:
        """Handle entity discovery event from device."""
        try:
            # Filter by node_id
            if event.data.get(CONF_NODE_ID, "") != node_id:
                return

            # Get coordinator from config_entry
            coordinator = config_entry.runtime_data
            if not coordinator:
                _LOGGER.error(
                    "No coordinator found for %s entity creation",
                    config.entity_name,
                )
                return

            # Check for duplicate using coordinator's cache
            # Use entity_id_suffix as cache key to separate different entity types
            cache_key = config.entity_id_suffix
            unique_id = f"{DOMAIN}_{node_id}_{config.entity_id_suffix}"
            discovered_cache = coordinator.discovered_entities.setdefault(cache_key, {})
            if unique_id in discovered_cache:
                return

            entity_kwargs: dict[str, Any] = {
                "coordinator": coordinator,
                CONF_NODE_ID: node_id,
                KEY_DEVICE_NAME: device_name,
                KEY_INITIAL_DATA: event.data.get(KEY_INITIAL_DATA),
            }

            # Add extra kwargs if provided
            if config.extra_entity_kwargs:
                entity_kwargs.update(config.extra_entity_kwargs(event.data))

            # Create entity instance
            entity = config.entity_class(**entity_kwargs)

            # Get async_add_entities callback from coordinator
            async_add_entities = coordinator.entity_callbacks.get(config.platform)

            if not async_add_entities:
                _LOGGER.error(
                    "%s async_add_entities callback not found in coordinator",
                    config.entity_name,
                )
                return

            # Add entity and track in coordinator's cache
            async_add_entities([entity])
            discovered_cache[unique_id] = entity

        except (TypeError, ValueError, KeyError, AttributeError):
            _LOGGER.exception("Failed to create %s entity", config.entity_name)

    # Register discovery listener
    config_entry.async_on_unload(
        hass.bus.async_listen(config.discovered_event, handle_entity_discovered)
    )


EntityFactory = Callable[
    [Mapping[str, Any], "ESPDataUpdateCoordinator", str, str | None],
    Any | None,
]


def setup_single_entity_discovery(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    result: "PlatformSetupResult",
    discovered_event: str,
    platform_name: str,
    entity_factory: EntityFactory,
) -> None:
    """Set up discovery listener for a single-entity platform."""

    @callback
    def handle_discovered(event: Event) -> None:
        """Handle entity discovery event."""
        try:
            # Filter by node_id
            if event.data.get(CONF_NODE_ID, "") != result.node_id:
                return

            # Check for duplicate
            entity_key = f"{result.node_id}_{platform_name}"
            if entity_key in result.discovered_entities:
                return

            # Get device name, use config_entry title if not in event
            device_name = event.data.get(KEY_DEVICE_NAME) or config_entry.title

            # Create entity via factory
            entity = entity_factory(
                event.data, result.coordinator, result.node_id, device_name
            )

            if entity is None:
                return

            # Track and add entity
            result.discovered_entities[entity_key] = entity
            result.async_add_entities([entity])

        except (TypeError, ValueError, KeyError, AttributeError):
            _LOGGER.exception("Error handling %s discovery", platform_name)

    # Register listener with automatic cleanup
    config_entry.async_on_unload(
        hass.bus.async_listen(discovered_event, handle_discovered)
    )


@dataclass
class PlatformSetupResult:
    """Result of platform setup initialization."""

    coordinator: "ESPDataUpdateCoordinator"
    node_id: str
    discovered_entities: dict[str, Any]
    async_add_entities: AddEntitiesCallback


PLATFORM_CACHE_KEYS: dict[str, str] = {
    PLATFORM_SENSOR: CACHE_SENSORS,
    PLATFORM_BINARY_SENSOR: CACHE_BINARY_SENSORS,
    PLATFORM_LIGHT: CACHE_LIGHTS,
    PLATFORM_NUMBER: CACHE_NUMBERS,
}


def setup_platform_discovery(
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform_name: str,
) -> PlatformSetupResult | None:
    """Initialize common platform discovery setup.

    Returns PlatformSetupResult or None if setup cannot proceed.
    """
    coordinator = config_entry.runtime_data
    if not coordinator:
        return None

    node_id = config_entry.data.get(CONF_NODE_ID)
    if not node_id:
        _LOGGER.debug(
            "Missing or empty node_id in config entry %s (data: %s)",
            config_entry.entry_id,
            {k: v for k, v in config_entry.data.items() if k != KEY_PASSWORD},
        )
        return None

    # Get cache from coordinator (auto-cleaned when config entry unloads)
    cache_key = PLATFORM_CACHE_KEYS.get(platform_name, platform_name)
    discovered_entities = coordinator.discovered_entities.setdefault(cache_key, {})

    # Store callback in coordinator (auto-cleaned when config entry unloads)
    coordinator.entity_callbacks[platform_name] = async_add_entities

    return PlatformSetupResult(
        coordinator=coordinator,
        node_id=node_id,
        discovered_entities=discovered_entities,
        async_add_entities=async_add_entities,
    )


__all__ = [
    "PLATFORM_CACHE_KEYS",
    "DiscoveryConfig",
    "EntityFactory",
    "PlatformSetupResult",
    "create_discovery_listener",
    "setup_platform_discovery",
    "setup_single_entity_discovery",
]
