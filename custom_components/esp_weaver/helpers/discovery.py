# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Wiring that turns a node's resolved bindings into a platform's entities.

Every platform supplies its name and entity class; the rest is shared.
"""

from collections.abc import Callable, Coroutine, Mapping
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..profiles import EntityBinding
from .ha_types import ESPConfigEntry
from .signals import signal_binding_discovered, signal_binding_removed

_LOGGER = logging.getLogger(__name__)


type PlatformSetup = Callable[
    [HomeAssistant, ESPConfigEntry, AddEntitiesCallback], Coroutine[Any, Any, None]
]


def binding_platform_setup(platform_name: str, entity_cls: type) -> PlatformSetup:
    """Return the ``async_setup_entry`` of a platform built from bindings."""

    async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ESPConfigEntry,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        setup_binding_platform(
            hass, config_entry, async_add_entities, platform_name, entity_cls
        )

    return async_setup_entry


def setup_binding_platform(
    hass: HomeAssistant,
    config_entry: ESPConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform_name: str,
    entity_cls: type,
) -> None:
    """Create this platform's entities from the bindings a node resolved.

    Entities are cached on the coordinator by binding key (the unique_id
    suffix), so a binding announced twice cannot produce two entities. Each
    gets the current report, so it starts with the values already published.
    """
    coordinator = config_entry.runtime_data
    node_id = coordinator.node_id
    discovered = coordinator.discovered_entities.setdefault(platform_name, {})

    @callback
    def handle_binding_discovered(
        binding: EntityBinding, report: Mapping[str, Any]
    ) -> None:
        """Create an entity for one resolved binding."""
        if binding.platform != platform_name or binding.key in discovered:
            return

        entity = entity_cls(
            coordinator=coordinator,
            binding=binding,
            device_name=config_entry.title,
            report=report,
        )
        discovered[binding.key] = entity
        async_add_entities([entity])

    @callback
    def handle_binding_removed(platform: str, key: str) -> None:
        """Drop the platform's record of an entity Home Assistant has removed.

        Otherwise the key would look taken to a later discovery.
        """
        if platform == platform_name and discovered.pop(key, None) is not None:
            _LOGGER.debug("Dropped %s %r from %s", platform_name, key, node_id)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_binding_discovered(node_id), handle_binding_discovered
        )
    )
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_binding_removed(node_id), handle_binding_removed
        )
    )


__all__ = ["setup_binding_platform"]
