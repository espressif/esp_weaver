# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Data update coordinator for ESP-Weaver integration."""

import asyncio
from collections.abc import Callable
import contextlib
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_IDENTITY_ADVERTISED,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    RECONNECT_DELAYS,
    REDISCOVERY_MIN_INTERVAL_SECONDS,
)
from .helpers.signals import signal_connection_error
from .identity import DOMAIN
from .iot.parsers.property_parser import model_id_from_config
from .polling import CloudPoller, DevicePoller, LocalPoller
from .profiles import DeviceIdentity

if TYPE_CHECKING:
    from .iot.client.device_api import ESPWeaverApi

_LOGGER = logging.getLogger(__name__)

# Default update interval as timedelta
DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS)


class ESPDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """The poll loop for one ESP-Weaver node.

    Discovers what the node is made of the first time it answers, publishes
    later reports to the entities over the dispatcher, and decides whether the
    node counts as available, reconnecting with a backoff when it stops
    answering. A refresh carries no data of its own.

    Availability ownership (who decides what):

    - **This coordinator** is the authority for entity ``available``. Platforms
      read ``coordinator.is_available``; they do not invent their own online
      flag.
    - **Local session** (registry client / ``is_device_available_async``) answers
      whether the current TCP session is usable. The poller and reconnect path
      consult it; they do not publish entity availability themselves.
    - **mDNS / TCP probes** (``AvailabilityManager``) find a moved or waking
      device and update ``device.ip``; reconnect then dials that address.
    - **CloudPoller** tracks RainMaker connectivity separately for cloud
      entries; entity availability still flows through ``_set_available`` here.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: "ESPWeaverApi",
        node_id: str,
        device_name: str,
        config_entry: ConfigEntry,
        identity: DeviceIdentity,
    ) -> None:
        """Initialize the coordinator for one node.

        Args:
            identity: What the bound product claims the device is, written to
                the device page once the node's reported model confirms it.
        """
        self.api = api
        self.node_id = node_id
        self.device_name = device_name
        self.identity = identity

        # How the node is reached is fixed by its config entry, so the poller
        # is chosen once.
        self._poller: DevicePoller = (
            CloudPoller(api, node_id)
            if api.is_cloud_device(node_id)
            else LocalPoller(api, node_id)
        )

        # Entity caches keyed by platform name, filled in as each platform sets
        # itself up and dropped with the coordinator when the entry unloads.
        self.discovered_entities: dict[str, dict[str, Any]] = {}

        # Whether discovery has run is not kept here: the API layer owns it, so
        # that a node discovered over one route is not rediscovered over another.
        # Start unavailable until a poll or reconnect proves the node answers.
        self._last_available = False
        # True after we have logged a loss, so recovery can log "back online"
        # without calling the first successful reach a recovery.
        self._unavailable_logged = False
        self._reconnect_attempt = 0
        self._reconnect_task: asyncio.Task[bool] | None = None
        # When the node's structure was last read again on an entity's
        # account (``time.monotonic``), or None if it never was.
        self._last_rediscovery: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{node_id}",
            update_interval=DEFAULT_UPDATE_INTERVAL,
            config_entry=config_entry,
        )

        # Home Assistant only polls a coordinator that has listeners, and the
        # listeners are entities that do not exist until discovery has run. A
        # node provisioned seconds ago has no config in the cloud yet, so the
        # first attempt comes back empty; this stand-in keeps polling going.
        self._discovery_hold: Callable[[], None] | None = self.async_add_listener(
            lambda: None
        )

        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                signal_connection_error(node_id),
                self._handle_connection_error,
            )
        )

    @callback
    def _handle_connection_error(self) -> None:
        """Note a connection this node's client has just lost.

        The backoff starts over: the node was reachable a moment ago, so the
        first attempt is due at once, whatever an earlier run got to.
        """
        if self._reconnecting:
            return

        self._set_available(False)
        self._reconnect_attempt = 0
        self._schedule_reconnection()

    @property
    def _reconnecting(self) -> bool:
        """Return True while an attempt to reach the device is pending or running."""
        return self._reconnect_task is not None and not self._reconnect_task.done()

    def _set_available(self, available: bool) -> None:
        """Set device availability state, without repainting entities.

        Publishing is left to the end of a refresh or to the reconnection
        attempt, which answers within half a second, so a blip is back before
        anything is shown. Loss and recovery are each logged once at info.

        Every answer from the node, not only the first after a loss, clears
        the backoff count: this is the one place it is reset.
        """
        if available:
            self._reconnect_attempt = 0

        if self._last_available == available:
            return

        was_available = self._last_available
        self._last_available = available

        if available:
            if self._unavailable_logged:
                _LOGGER.info("Device %s is back online", self.node_id)
                self._unavailable_logged = False
        elif was_available:
            _LOGGER.info("Device %s is unavailable", self.node_id)
            self._unavailable_logged = True

    def _release_discovery_hold(self) -> None:
        """Stop standing in for the entities; polling follows them from here."""
        if self._discovery_hold is None:
            return

        self._discovery_hold()
        self._discovery_hold = None

    def _schedule_reconnection(self, delay: float = 0.5) -> None:
        """Try to reach a local device again in ``delay`` seconds, if nothing already is."""
        if self._reconnecting or not self._poller.reconnects:
            return

        self._reconnect_task = self.hass.async_create_task(
            self._delayed_reconnection(delay),
            name=f"{DOMAIN}_reconnect_{self.node_id}",
        )

    async def _delayed_reconnection(self, delay: float) -> bool:
        """Wait out the backoff, then make one attempt.

        Returns:
            True if the device is available again.
        """
        current_task = self._reconnect_task
        try:
            await asyncio.sleep(delay)
            return await self._attempt_single_reconnection()
        finally:
            # Only clear if no new task was scheduled during reconnection
            if self._reconnect_task is current_task:
                self._reconnect_task = None

    async def _async_update_data(self) -> None:
        """Run one poll of the device.

        The clients report failure by their return values, so a poll does not
        normally raise; one that does is read as the node being unreachable,
        so that entities go unavailable rather than keep a stale state.

        Raises:
            UpdateFailed: If a cloud node has withheld its config for too long.
        """
        try:
            await self._refresh_once()
        except (OSError, ClientError) as err:
            _LOGGER.debug("Poll of %s raised: %s", self.node_id, err)
            self._set_available(False)
            self._schedule_reconnection()

    async def _refresh_once(self) -> None:
        """Run one poll: discover if needed, then read the device's state.

        Raises:
            UpdateFailed: If a cloud node has withheld its config for too long.
        """
        if not await self._poller.is_reachable():
            self._set_available(False)
            self._schedule_reconnection()
            return

        discovery_done, just_discovered = await self._ensure_discovered()

        if not discovery_done:
            self._poller.discovery_pending()
            return
        if not just_discovered:
            # A whole cycle after discovery the entities it announced are
            # listening (they are built from an event, after the refresh that
            # fired it), so the stand-in can go.
            self._release_discovery_hold()

            if not await self._poller.poll():
                self._set_available(False)
                self._schedule_reconnection()
                return

        self._poller.polled_ok()
        self._set_available(True)
        self._publish_reported_identity()

    async def async_resync_structure(self) -> None:
        """Read what the node is made of again, after a write found nothing to address.

        An entity whose parameter the node did not report is one the firmware
        has changed under the profile - after an OTA, say. Reading the node
        again rebuilds its entities from what it reports now; at most once a
        minute, so that a row of refused writes costs one read.
        """
        now = time.monotonic()
        if (
            self._last_rediscovery is not None
            and now - self._last_rediscovery < REDISCOVERY_MIN_INTERVAL_SECONDS
        ):
            return
        self._last_rediscovery = now

        _LOGGER.debug("Reading the structure of %s again", self.node_id)
        await self._poller.discover()

    def _publish_reported_identity(self) -> None:
        """Keep the device page current: the binding's side and the node's.

        Manufacturer and model are the bound product's vendor and label -
        what the device is treated as - and follow a rebind. The firmware
        version and the model id are the node's own, from its config: the
        model the firmware set on purpose, or none. Every poll, not only the
        first: an OTA changes both, and the config read after the reboot is
        the only announcement. Written only when a value differs.
        """
        version = self.api.get_firmware_version(self.node_id)
        config = self.api.get_reported_config(self.node_id)[0]
        if not version and not config:
            return

        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.node_id)})
        if device is None:
            return

        changes: dict[str, str | None] = {}
        if version and device.sw_version != version:
            changes["sw_version"] = version
        if config:
            identity = self.identity
            wanted = {
                "manufacturer": identity.manufacturer,
                "model": identity.model,
                "model_id": model_id_from_config(
                    config,
                    identity_advertised=self.config_entry.data.get(
                        CONF_IDENTITY_ADVERTISED, True
                    ),
                ),
            }
            changes |= {
                field: value
                for field, value in wanted.items()
                if getattr(device, field) != value
            }
        if not changes:
            return

        registry.async_update_device(
            device.id,
            sw_version=changes.get("sw_version", UNDEFINED),
            manufacturer=changes.get("manufacturer", UNDEFINED),
            model=changes.get("model", UNDEFINED),
            model_id=changes.get("model_id", UNDEFINED),
        )

    async def _ensure_discovered(self) -> tuple[bool, bool]:
        """Discover the node if it has not been.

        Returns:
            Whether discovery has completed, and whether this call completed it.
        """
        if self.api.is_discovery_completed(self.node_id):
            return True, False

        await self._poller.discover()

        just_discovered = self.api.is_discovery_completed(self.node_id)
        return just_discovered, just_discovered

    async def _attempt_single_reconnection(self) -> bool:
        """Make one attempt to reach the device, scheduling the next if it fails.

        Returns:
            True if the device is available again.
        """
        try:
            if await self._try_reconnect():
                return True
        except (OSError, ClientError) as err:
            _LOGGER.debug("Reconnection attempt for %s failed: %s", self.node_id, err)
        # One bad attempt must not end the retries.
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Unexpected error during reconnection for %s", self.node_id
            )
        self._schedule_next_reconnection_if_needed()
        return False

    async def _try_reconnect(self) -> bool:
        """Try once to reach the device.

        Returns:
            True if it never really left, the registry holds a live connection,
            or it answered mDNS and accepted a fresh one.
        """
        if self._last_available:
            return True

        device = self.api.get_device(self.node_id)
        if device is None or not device.ip:
            return False

        if await self.api.is_device_available_async(self.node_id):
            self._mark_reconnected()
            return True

        if not await self.api.is_mdns_available(self.node_id, device.ip):
            return False

        # The mDNS check follows a device that has moved and records its new
        # address, so the address to dial is read back rather than reused.
        device = self.api.get_device(self.node_id)
        if device is None or not device.ip:
            return False
        if await self.api.register_device(
            self.node_id, device.ip, device.port or self.api.default_port
        ):
            self._mark_reconnected()
            return True

        return False

    def _mark_reconnected(self) -> None:
        """Note the device as back, and refresh so its entities hear about it.

        ``async_refresh`` logs its own failures rather than raising them.
        """
        self._set_available(True)
        self.hass.async_create_task(
            self.async_refresh(), name=f"{DOMAIN}_refresh_{self.node_id}"
        )

    def _schedule_next_reconnection_if_needed(self) -> None:
        """Schedule next reconnection attempt if retries remain."""
        # Nothing else would repaint until the next poll, up to a minute away.
        self.async_update_listeners()

        if self._reconnect_attempt < len(RECONNECT_DELAYS):
            delay = RECONNECT_DELAYS[self._reconnect_attempt]
            self._reconnect_attempt += 1
            self._schedule_reconnection(delay)
        else:
            self._reconnect_attempt = 0

    @property
    def is_available(self) -> bool:
        """Return True if device is available."""
        return self._last_available

    @property
    def firmware_version(self) -> str | None:
        """Return the firmware version the node reported, if any."""
        return self.api.get_firmware_version(self.node_id)

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources.

        Event listeners are automatically cleaned up via config_entry.async_on_unload().
        """
        self._release_discovery_hold()

        # Cancel pending reconnection task
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        # Call parent shutdown to cancel any pending refresh
        await super().async_shutdown()
