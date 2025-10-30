# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The ESP-Weaver integration."""

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import battery_energy, imu_gesture, interactive_input, low_power_sleep
from .const import DEVICE_SETUP_TIMEOUT, PLATFORMS
from .coordinator import ESPDataUpdateCoordinator
from .helpers.event_dispatcher import create_event_dispatcher
from .helpers.ha_types import ESPConfigEntry
from .iot.client.device_api import ESPWeaverApi
from .iot.specs.device_specs import (
    DEFAULT_DEVICE_NAME_PREFIX,
    DEFAULT_MANUFACTURER,
    DEFAULT_PORT,
)
from .iot.specs.events import DOMAIN
from .iot.specs.keys import CONF_NODE_ID, KEY_API

_LOGGER = logging.getLogger(__name__)

# Schema for configuration.yaml (config entry only)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new version.

    Args:
        hass: Home Assistant instance.
        config_entry: The config entry to migrate.

    Returns:
        True if migration successful, False otherwise.
    """
    _LOGGER.debug(
        "Migrating from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version > 1:
        # Future versions not supported
        return False

    # If already at current or newer minor version, no migration needed
    if config_entry.version == 1 and config_entry.minor_version >= 1:
        return True

    # Version 1.0 -> 1.1: Update minor version
    hass.config_entries.async_update_entry(config_entry, version=1, minor_version=1)
    _LOGGER.info(
        "Migrated config entry to version %s.%s",
        1,
        1,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ESPConfigEntry) -> bool:
    """Set up ESP-Weaver device from a config entry."""
    node_id = entry.data.get(CONF_NODE_ID)
    host = entry.data.get(CONF_HOST)

    if not node_id or not host:
        _LOGGER.error("Missing required config: node_id=%s, host=%s", node_id, host)
        return False

    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    # Initialize domain data structure
    domain_data = hass.data.setdefault(DOMAIN, {})

    # Validation passed, now create/get API
    # Initialize or reuse shared API
    api = await _get_or_create_api(hass, domain_data)

    # Register config entry with API first (for proper association)
    api.register_config_entry(node_id, entry.entry_id)

    # Create coordinator
    coordinator = ESPDataUpdateCoordinator(
        hass=hass,
        api=api,
        node_id=node_id,
        device_name=entry.title,
        config_entry=entry,
    )
    entry.runtime_data = coordinator

    # Register device in Home Assistant device registry
    _register_device(hass, entry, node_id, host, port)

    # Setup platforms (registers discovery listeners)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Setup discovery listeners for additional entity types
    _setup_discovery_listeners(hass, node_id, entry)

    # Start mDNS services AFTER platforms are set up
    # This ensures discovery listeners are ready before mDNS triggers device connections
    await api.start_services()

    # Connect to device (discovery events will be received by ready listeners)
    # Use process_properties=False for initial setup because entities don't exist yet.
    # Entities will get initial values from *_discovered event data.
    try:
        async with asyncio.timeout(DEVICE_SETUP_TIMEOUT):
            await api.register_device(node_id, host, port, process_properties=False)
    except TimeoutError:
        _LOGGER.debug(
            "Initial device setup timed out for %s at %s:%s, will retry via mDNS",
            node_id,
            host,
            port,
        )
    except (OSError, ConnectionError, ClientError) as err:
        # OSError: network-level errors (socket errors, DNS failures)
        # ConnectionError: connection refused, reset, etc.
        # ClientError: aiohttp client errors
        _LOGGER.debug(
            "Initial device setup failed for %s at %s:%s: %s, will retry via mDNS",
            node_id,
            host,
            port,
            err,
        )

    # Perform first refresh - this triggers entity discovery
    # We don't raise ConfigEntryNotReady here because ESP devices may be offline
    # and will be discovered via mDNS when they come online
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        _LOGGER.info("Initial coordinator refresh not ready, will retry via mDNS")
    except UpdateFailed:
        _LOGGER.info("Initial coordinator refresh failed, will retry via mDNS")

    return True


async def _get_or_create_api(
    hass: HomeAssistant,
    domain_data: dict[str, Any],
) -> ESPWeaverApi:
    """Get existing API or create a new one."""
    if KEY_API in domain_data:
        existing_api: ESPWeaverApi = domain_data[KEY_API]
        return existing_api

    # Create event dispatcher and inject into API
    event_dispatcher = create_event_dispatcher(hass)
    api = ESPWeaverApi(hass, DOMAIN, event_dispatcher=event_dispatcher)
    domain_data[KEY_API] = api
    return api


def _register_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    node_id: str,
    host: str,
    port: int,
) -> None:
    """Register device in Home Assistant device registry."""
    device_registry = dr.async_get(hass)

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, node_id)},
        name=entry.title,
        manufacturer=DEFAULT_MANUFACTURER,
        model=f"{DEFAULT_DEVICE_NAME_PREFIX}{node_id}",
        configuration_url=f"http://{host}:{port}",
    )


def _setup_discovery_listeners(
    hass: HomeAssistant,
    node_id: str,
    entry: ConfigEntry,
) -> None:
    """Setup discovery listeners for additional entity types."""
    battery_energy.setup_discovery_listener(hass, node_id, entry)
    imu_gesture.setup_discovery_listener(hass, node_id, entry)
    interactive_input.setup_discovery_listener(hass, node_id, entry)
    low_power_sleep.setup_discovery_listener(hass, node_id, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ESPConfigEntry) -> bool:
    """Unload an ESP-Weaver config entry."""
    # 1. Unload platforms first (while coordinator is still available)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    node_id = entry.data.get(CONF_NODE_ID)
    domain_data = hass.data.get(DOMAIN, {})
    api = domain_data.get(KEY_API)

    # 2. Unregister device from API (don't let errors prevent cleanup)
    if node_id and api:
        try:
            await api.unregister_device(node_id)
        except (OSError, TimeoutError, RuntimeError) as err:
            _LOGGER.error("Error unregistering device %s: %s", node_id, err)

    # 3. Shutdown coordinator (discovered_entities and entity_callbacks auto-cleaned)
    coordinator = entry.runtime_data
    if coordinator:
        await coordinator.async_shutdown()

    # 4. Clean up integration if no entries remain
    remaining_entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining_entries and DOMAIN in hass.data:
        await _cleanup_integration(hass, api)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    _device_entry: dr.DeviceEntry,
) -> bool:
    """Remove a device from the config entry.

    This is called when a user removes a device from the device registry.
    We allow removal if the device is no longer connected/available.

    Args:
        hass: Home Assistant instance.
        config_entry: The config entry for this device.
        device_entry: The device entry to remove.

    Returns:
        True if the device can be removed, False otherwise.
    """
    node_id = config_entry.data.get(CONF_NODE_ID)
    domain_data = hass.data.get(DOMAIN, {})
    api = domain_data.get(KEY_API)

    # Check if device is currently available
    if api and node_id:
        is_available = api.is_device_available(node_id)
        if is_available:
            # Device is still connected, don't allow removal
            _LOGGER.warning(
                "Cannot remove device %s: device is still connected",
                node_id,
            )
            return False

    # Device is offline or unknown, allow removal
    return True


async def _cleanup_integration(
    hass: HomeAssistant,
    api: ESPWeaverApi | None,
) -> None:
    """Clean up integration data when all entries are unloaded."""
    if api:
        await api.cleanup()
    hass.data.pop(DOMAIN, None)
