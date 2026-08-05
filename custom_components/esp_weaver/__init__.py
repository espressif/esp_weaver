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
from .iot.cloud import RainMakerCLIClient
from .iot.specs.device_specs import (
    DEFAULT_DEVICE_NAME_PREFIX,
    DEFAULT_MANUFACTURER,
    DEFAULT_PORT,
)
from .iot.specs.events import DOMAIN
from .iot.specs.keys import (
    CONF_CONNECTION_MODE,
    CONF_NODE_ID,
    CONF_RAINMAKER_REGION,
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
    KEY_API,
    RAINMAKER_REGION_GLOBAL,
)

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
    connection_mode = entry.data.get(CONF_CONNECTION_MODE, CONNECTION_MODE_LOCAL)

    if not node_id:
        _LOGGER.error("Missing required config: node_id=%s", node_id)
        return False

    domain_data = hass.data.setdefault(DOMAIN, {})
    api = await _get_or_create_api(hass, domain_data)

    if connection_mode == CONNECTION_MODE_CLOUD:
        return await _setup_cloud_device(hass, entry, api, node_id, domain_data)

    return await _setup_local_device(hass, entry, api, node_id)


async def _setup_cloud_device(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: ESPWeaverApi,
    node_id: str,
    domain_data: dict[str, Any],
) -> bool:
    """Set up a cloud-controlled device."""
    cloud_client = await _get_or_create_cli_client(domain_data, entry)
    if cloud_client is None:
        raise ConfigEntryNotReady("Failed to create RainMaker CLI client")

    api.set_cloud_client(node_id, cloud_client)
    api.register_config_entry(node_id, entry.entry_id, CONNECTION_MODE_CLOUD)

    coordinator = _create_coordinator(hass, api, node_id, entry)
    _register_device(hass, entry, node_id, is_cloud=True)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _setup_discovery_listeners(hass, node_id, entry)

    api.register_cloud_device(node_id)

    # Let ConfigEntryNotReady propagate so HA retries setup with backoff.
    # Cloud discovery failures are handled gracefully inside the coordinator:
    # _async_update_data returns CoordinatorData(available=False) for the first
    # few attempts, only raising UpdateFailed after CLOUD_DISCOVERY_FAILURE_THRESHOLD.
    await coordinator.async_config_entry_first_refresh()

    return True


async def _get_or_create_cli_client(
    domain_data: dict[str, Any],
    entry: ESPConfigEntry,
) -> RainMakerCLIClient | None:
    """Get existing CLI client or create a new one."""
    region = entry.data.get(CONF_RAINMAKER_REGION, RAINMAKER_REGION_GLOBAL)
    cli_key = f"cli_client_{region}"

    if cli_key in domain_data:
        client: RainMakerCLIClient = domain_data[cli_key]
        return client

    client = RainMakerCLIClient(region=region)

    # Verify we can get a session
    if not await client.check_health():
        _LOGGER.error(
            "Failed to connect to RainMaker for region %s. Please re-login.",
            region,
        )
        return None

    domain_data[cli_key] = client
    return client


async def _setup_local_device(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: ESPWeaverApi,
    node_id: str,
) -> bool:
    """Set up a locally-controlled device."""
    host = entry.data.get(CONF_HOST)

    if not host:
        _LOGGER.error("Missing required config for local device: host=%s", host)
        return False

    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    api.register_config_entry(node_id, entry.entry_id, CONNECTION_MODE_LOCAL)

    coordinator = _create_coordinator(hass, api, node_id, entry)
    _register_device(hass, entry, node_id, host, port)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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


def _create_coordinator(
    hass: HomeAssistant,
    api: ESPWeaverApi,
    node_id: str,
    entry: ESPConfigEntry,
) -> ESPDataUpdateCoordinator:
    """Create coordinator and bind it to the config entry."""
    coordinator = ESPDataUpdateCoordinator(
        hass=hass,
        api=api,
        node_id=node_id,
        device_name=entry.title,
        config_entry=entry,
    )
    entry.runtime_data = coordinator
    return coordinator


async def _get_or_create_api(
    hass: HomeAssistant,
    domain_data: dict[str, Any],
) -> ESPWeaverApi:
    """Get existing API or create a new one."""
    if KEY_API in domain_data:
        existing_api: ESPWeaverApi = domain_data[KEY_API]
        return existing_api

    event_dispatcher = create_event_dispatcher(hass)
    api = ESPWeaverApi(hass, DOMAIN, event_dispatcher=event_dispatcher)
    domain_data[KEY_API] = api
    return api


def _register_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    node_id: str,
    host: str = "",
    port: int = 0,
    *,
    is_cloud: bool = False,
) -> None:
    """Register device in Home Assistant device registry."""
    device_registry = dr.async_get(hass)
    model_suffix = " (Cloud)" if is_cloud else ""
    configuration_url = None
    if not is_cloud and host and port > 0:
        configuration_url = f"http://{host}:{port}"

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, node_id)},
        name=entry.title,
        manufacturer=DEFAULT_MANUFACTURER,
        model=f"{DEFAULT_DEVICE_NAME_PREFIX}{node_id}{model_suffix}",
        configuration_url=configuration_url,
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

    # Check if device is currently available (use async for cloud online check)
    if api and node_id:
        is_available = await api.is_device_available_async(node_id)
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
