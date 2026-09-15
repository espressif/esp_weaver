# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The ESP-Weaver integration."""

import asyncio
from collections.abc import Callable, Coroutine
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, SERVICE_RELOAD
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed
import voluptuous as vol

from .const import (
    CONF_CONNECTION_MODE,
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_RAINMAKER_REGION,
    CONF_SECURITY_VERSION,
    DEVICE_SETUP_TIMEOUT,
    PLATFORMS,
)
from .coordinator import ESPDataUpdateCoordinator
from .data import DomainData, domain_data, drop_domain_data
from .helpers.entries import active_entries, entry_for_node
from .helpers.event_dispatcher import create_event_dispatcher
from .helpers.ha_types import ESPConfigEntry
from .helpers.host import HomeAssistantHost
from .identity import DOMAIN
from .intents import async_setup_intents
from .iot.client.device_api import ESPWeaverApi
from .iot.cloud import RainMakerCLIClient
from .iot.specs.keys import (
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
    RAINMAKER_REGION_GLOBAL,
)
from .profiles import (
    OPTION_PROFILE,
    DeviceIdentity,
    EntityBinding,
    ProfileMissingError,
    async_get_catalog,
    async_import_profile,
    async_prune_store,
    async_refresh_catalog,
    async_registry_for_candidate,
    async_registry_for_entry,
    async_stored_product_for_entry,
    async_sync_binding_issues,
    resolve_bindings,
)

_LOGGER = logging.getLogger(__name__)

# Schema for configuration.yaml (config entry only)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_URL = "url"
ATTR_FILENAME = "filename"
ATTR_OVERWRITE = "overwrite"
ATTR_FILE = "file"
ATTR_PROFILES = "profiles"

# All fields optional: empty re-reads the profile directory (which is how
# "Reload all YAML configuration" calls it), a URL downloads a profile first.
RELOAD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_URL): vol.All(cv.url, vol.Length(max=2048)),
        vol.Optional(ATTR_FILENAME): cv.string,
        vol.Optional(ATTR_OVERWRITE, default=False): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and intents, once per run.

    They outlive any entry, and registering an intent twice logs a warning.
    """
    _register_services(hass)
    await async_setup_intents(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ESPConfigEntry) -> bool:
    """Set up ESP-Weaver device from a config entry."""
    node_id = entry.data[CONF_NODE_ID]
    connection_mode = entry.data[CONF_CONNECTION_MODE]

    data = domain_data(hass)
    api = await _get_or_create_api(hass, data)

    # Scan the profile directories once and cache the catalog; checking the
    # bindings against it is what raises "a newer profile is available".
    catalog = await async_get_catalog(hass)
    async_sync_binding_issues(hass, catalog)
    _async_start_repository_refresh(hass, data)

    # Only the content the device was bound to; nothing is substituted. Without
    # it this raises ConfigEntryError and the entry fails to set up. The same
    # content is read for what the device page says the device is.
    product = await async_stored_product_for_entry(hass, entry)
    await async_registry_for_candidate(hass, product)
    identity = DeviceIdentity.of(node_id, product, hass.config.language)

    # Only a changed binding needs a reload; the other options are read live.
    entry.async_on_unload(entry.add_update_listener(_async_binding_changed))

    if connection_mode == CONNECTION_MODE_CLOUD:
        await _setup_cloud_device(hass, entry, api, node_id, data, identity)
    else:
        await _setup_local_device(hass, entry, api, node_id, identity)

    # Recorded once setup can no longer fail: a failed setup is not unloaded,
    # and a binding left here would be read as the one that is running.
    data.loaded_bindings[entry.entry_id] = entry.options.get(OPTION_PROFILE)
    return True


@callback
def _async_start_repository_refresh(hass: HomeAssistant, data: DomainData) -> None:
    """Refresh the repository copy in the background, once per run.

    Setup does not wait for the network. Once the copy is current, the
    bindings are checked against it, which raises the update repairs.
    """
    if data.repository_refresh_started:
        return
    data.repository_refresh_started = True

    async def _refresh() -> None:
        catalog = await async_refresh_catalog(hass, online=True)
        async_sync_binding_issues(hass, catalog)

    hass.async_create_background_task(_refresh(), f"{DOMAIN} repository refresh")


async def _async_binding_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set the entry up again if its product binding changed, pruning the old copy."""
    loaded = domain_data(hass).loaded_bindings.get(entry.entry_id)
    if entry.options.get(OPTION_PROFILE) == loaded:
        return
    _LOGGER.info("Profile binding of %s changed, reloading it", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
    await async_prune_store(hass)


def _register_services(hass: HomeAssistant) -> None:
    """Register the integration-wide service.

    Named ``reload`` so Developer tools' "Reload all YAML configuration"
    includes it. Admin only, because it fetches a URL of the caller's choosing
    and writes into the configuration directory; Home Assistant enforces that
    only for signed-in users, so automations and the reload sweep are unaffected.
    """

    async def handle_reload(call: ServiceCall) -> ServiceResponse:
        """Handle esp_weaver.reload, with or without a profile to fetch."""
        response: dict[str, Any] = {}

        if url := call.data.get(ATTR_URL):
            path = await async_import_profile(
                hass,
                url,
                call.data.get(ATTR_FILENAME),
                call.data.get(ATTR_OVERWRITE, False),
            )
            response[ATTR_FILE] = str(path)
        elif call.data.get(ATTR_FILENAME):
            # Naming a file to write without saying where to fetch it from is a
            # half-filled form, and reloading anyway would look like it worked.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="reload_filename_without_url",
            )

        # Rereading picks up edited or added files. It changes no binding, but
        # raises the repair offering new content to the devices on a product.
        catalog = await async_refresh_catalog(hass, online=True)
        async_sync_binding_issues(hass, catalog)
        await asyncio.gather(
            *(
                hass.config_entries.async_reload(entry.entry_id)
                for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            )
        )

        # Home Assistant rejects a None response when one was asked for.
        response[ATTR_PROFILES] = [c.id for c in catalog.sorted()]
        return response

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        handle_reload,
        schema=RELOAD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


# Opens the way to a node once its platforms are listening; see _setup_device.
type Connect = Callable[[], Coroutine[Any, Any, None]]


async def _setup_device(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: ESPWeaverApi,
    node_id: str,
    identity: DeviceIdentity,
    connect: Connect,
    host: str = "",
    port: int = 0,
) -> None:
    """Set a node up the same way whichever way it is reached.

    Coordinator and device record first, then the platforms, then the
    connection, then the first refresh. The connection comes after the
    platforms so that nothing can be discovered before they are listening;
    and nothing after ``_forward_platforms`` may raise, which ``connect`` and
    ``_first_refresh`` each see to.
    """
    coordinator = _create_coordinator(hass, api, node_id, entry, identity)
    _register_device(hass, entry, node_id, identity, host, port)

    await _forward_platforms(hass, entry)

    await connect()
    await _first_refresh(coordinator, node_id)


async def _setup_cloud_device(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: ESPWeaverApi,
    node_id: str,
    data: DomainData,
    identity: DeviceIdentity,
) -> bool:
    """Set up a device reached through the cloud.

    Raises:
        ConfigEntryNotReady: if the CLI has no usable session for that region.
    """
    cloud_client = await _get_or_create_cli_client(data, entry)
    api.set_cloud_client(node_id, cloud_client)
    api.set_connection_mode(node_id, CONNECTION_MODE_CLOUD)

    async def connect() -> None:
        # Cloud discovery failures are handled inside the coordinator, which
        # stays unavailable for the first few attempts and only fails after
        # CLOUD_DISCOVERY_FAILURE_THRESHOLD.
        api.register_cloud_device(node_id)

    await _setup_device(hass, entry, api, node_id, identity, connect)
    return True


async def _get_or_create_cli_client(
    data: DomainData,
    entry: ESPConfigEntry,
) -> RainMakerCLIClient:
    """Return the CLI client for the entry's region, creating it on first use.

    Raises:
        ConfigEntryNotReady: if the CLI has no usable session for that region.
    """
    region = entry.data.get(CONF_RAINMAKER_REGION, RAINMAKER_REGION_GLOBAL)

    if (client := data.cloud_clients.get(region)) is not None:
        return client

    client = RainMakerCLIClient(region=region)
    if not await client.check_health():
        raise ConfigEntryNotReady(
            f"RainMaker CLI has no session for region {region}; log in again"
        )

    data.cloud_clients[region] = client
    return client


async def _setup_local_device(
    hass: HomeAssistant,
    entry: ESPConfigEntry,
    api: ESPWeaverApi,
    node_id: str,
    identity: DeviceIdentity,
) -> bool:
    """Set up a device reached over local control, at its configured address."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    api.set_connection_mode(node_id, CONNECTION_MODE_LOCAL)

    async def connect() -> None:
        # Starting mDNS is what lets a node be found after an address change;
        # start_services logs its own failure, and the node can still be
        # reached at its configured address without it.
        await api.start_services()
        await _connect_local_device(api, node_id, host, port)

    await _setup_device(hass, entry, api, node_id, identity, connect, host, port)
    return True


async def _forward_platforms(hass: HomeAssistant, entry: ESPConfigEntry) -> None:
    """Start every platform, before anything can be discovered.

    Nothing replays the binding-discovered signal, so discovery has to come
    after this. And nothing after this may raise: Home Assistant does not unload
    forwarded platforms when setup fails, so a retry would forward them twice
    and leave the entry in SETUP_ERROR until restart.
    """
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def _connect_local_device(
    api: ESPWeaverApi,
    node_id: str,
    host: str,
    port: int,
) -> None:
    """Open the session to a node at its configured address.

    Properties are not processed: the entities do not exist yet and get their
    first values from discovery. An unreachable node is not an error; mDNS
    picks it up when it comes back.
    """
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
    except (OSError, ClientError, RuntimeError) as err:
        # The client reports a failed session rather than raising it; this is
        # the net under anything that still gets out.
        _LOGGER.debug(
            "Initial device setup failed for %s at %s:%s: %s, will retry via mDNS",
            node_id,
            host,
            port,
            err,
        )


async def _first_refresh(
    coordinator: ESPDataUpdateCoordinator,
    node_id: str,
) -> None:
    """Run the discovery refresh without letting a failure out.

    Polling carries on and recovers on its own; an exception here would leave
    the entry in SETUP_ERROR with its platforms already forwarded.
    """
    try:
        await coordinator.async_config_entry_first_refresh()
    except (ConfigEntryNotReady, UpdateFailed) as err:
        _LOGGER.info(
            "Initial refresh for %s did not succeed (%s), will retry when it answers",
            node_id,
            err,
        )


def _create_coordinator(
    hass: HomeAssistant,
    api: ESPWeaverApi,
    node_id: str,
    entry: ESPConfigEntry,
    identity: DeviceIdentity,
) -> ESPDataUpdateCoordinator:
    """Create coordinator and bind it to the config entry."""
    coordinator = ESPDataUpdateCoordinator(
        hass=hass,
        api=api,
        node_id=node_id,
        device_name=entry.title,
        config_entry=entry,
        identity=identity,
    )
    entry.runtime_data = coordinator
    return coordinator


async def _get_or_create_api(
    hass: HomeAssistant,
    data: DomainData,
) -> ESPWeaverApi:
    """Get existing API or create a new one."""
    if data.api is not None:
        return data.api

    event_dispatcher = create_event_dispatcher(hass)

    def device_auth(node_id: str) -> tuple[str, int]:
        """Look up the PoP and security version stored on a config entry."""
        entry = entry_for_node(hass, node_id)
        if entry is None:
            return ("", 1)
        return (
            entry.data.get(CONF_CUSTOM_POP, ""),
            entry.data.get(CONF_SECURITY_VERSION, 1),
        )

    async def resolve(
        node_id: str,
        raw_config: dict[str, Any],
        raw_params: dict[str, Any],
    ) -> list[EntityBinding]:
        """Match the product this node is bound to against what it reported."""
        entry = entry_for_node(hass, node_id)
        if entry is None:
            _LOGGER.warning(
                "Node %s has no config entry, so no profile applies", node_id
            )
            return []
        try:
            registry = await async_registry_for_entry(hass, entry)
        except ProfileMissingError as err:
            # Setup fails on the same error, so a report reaching here means
            # the store went away under a running entry. Announce nothing.
            _LOGGER.error("Node %s cannot be resolved: %s", node_id, err)
            return []
        return resolve_bindings(node_id, raw_config, raw_params, registry)

    api = ESPWeaverApi(
        HomeAssistantHost(hass),
        event_dispatcher=event_dispatcher,
        device_auth=device_auth,
        resolve_bindings=resolve,
    )
    data.api = api
    return api


def _register_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    node_id: str,
    identity: DeviceIdentity,
    host: str = "",
    port: int = 0,
) -> None:
    """Register the node in the Home Assistant device registry.

    Serial number, manufacturer and model: the node id and the vendor and
    label of the product picked, known before the node is reached. The
    firmware version and the model id are the node's own, and wait for the
    coordinator to read its config. A cloud node has no address, so no
    configuration URL.
    """
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, node_id)},
        name=entry.title,
        serial_number=identity.serial_number,
        manufacturer=identity.manufacturer,
        model=identity.model,
        configuration_url=f"http://{host}:{port}" if host and port > 0 else None,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ESPConfigEntry) -> bool:
    """Unload an ESP-Weaver config entry."""
    # Platforms go first, while the coordinator their entities read is still there.
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    node_id = entry.data[CONF_NODE_ID]
    data = domain_data(hass)
    api = data.api

    # The coordinator stops before its device goes: a reconnection attempt
    # still running would otherwise find the registry empty under it.
    if coordinator := entry.runtime_data:
        await coordinator.async_shutdown()

    if api:
        try:
            await api.unregister_device(node_id)
        except (OSError, RuntimeError) as err:
            # Said out loud, but not allowed to stop the rest of the teardown.
            _LOGGER.error("Error unregistering device %s: %s", node_id, err)

    data.loaded_bindings.pop(entry.entry_id, None)

    # Only entries that are up share the API. One that failed to set up holds
    # nothing, and must not keep the mDNS browser alive after the last
    # working entry has gone.
    others_remain = any(
        other.entry_id != entry.entry_id for other in active_entries(hass)
    )
    if not others_remain:
        await _cleanup_integration(hass, api)

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget what a deleted device was bound to, if nothing else shares it.

    The update repairs are checked again: one raised for this device's
    product would otherwise stand until the next entry sets up.
    """
    await async_prune_store(hass)
    async_sync_binding_issues(hass, await async_get_catalog(hass))


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    _device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting a device unless its node is still answering."""
    node_id = config_entry.data[CONF_NODE_ID]
    api = domain_data(hass).api

    if api and await api.is_device_available_async(node_id):
        _LOGGER.warning("Cannot remove device %s: it is still connected", node_id)
        return False

    return True


async def _cleanup_integration(
    hass: HomeAssistant,
    api: ESPWeaverApi | None,
) -> None:
    """Drop the device API and profile caches once the last entry is unloaded.

    The services stay: whoever deleted their last device still needs reload.
    """
    if api:
        await api.cleanup()
    drop_domain_data(hass)
