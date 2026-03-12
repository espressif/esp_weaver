# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Data update coordinator for ESP-Weaver integration."""

import asyncio
from collections.abc import Callable
import contextlib
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any, Final

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CACHE_BINARY_SENSORS,
    CACHE_LIGHTS,
    CACHE_NUMBERS,
    CACHE_SENSORS,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
    RECONNECT_DELAYS,
)
from .helpers.ha_types import CoordinatorData
from .iot.specs.events import DOMAIN, EVENT_CONNECTION_ERROR
from .iot.specs.keys import CONF_NODE_ID, KEY_IP, KEY_PORT

if TYPE_CHECKING:
    from .iot.client.device_api import ESPWeaverApi

_LOGGER = logging.getLogger(__name__)

# Default update interval as timedelta
DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS)


class ESPDataUpdateCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """ESP-Weaver data update coordinator.

    Manages data updates from ESP devices, handling:
    - Entity discovery and registration
    - Device availability monitoring
    - Automatic reconnection with exponential backoff
    - Connection error recovery
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: "ESPWeaverApi",
        node_id: str,
        device_name: str,
        config_entry: ConfigEntry,
        update_interval: timedelta | None = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance.
            api: ESP Home API instance.
            node_id: Device node ID.
            device_name: Device display name.
            config_entry: Config entry for this device.
            update_interval: Optional custom update interval.
        """
        self.api = api
        self.node_id = node_id
        self.device_name = device_name

        # Entity discovery caches (per-platform, auto-cleaned on config entry unload)
        self.discovered_entities: dict[str, dict[str, Any]] = {
            CACHE_SENSORS: {},
            CACHE_BINARY_SENSORS: {},
            CACHE_LIGHTS: {},
            CACHE_NUMBERS: {},
        }
        # Platform entity add callbacks
        self.entity_callbacks: dict[str, Callable] = {}

        # Internal state
        # Note: Discovery state is managed by API layer (single source of truth)
        # Use self.api.is_discovery_completed(self.node_id) to check
        self._last_available = True
        self._consecutive_failures = 0
        self._reconnect_attempt = 0
        self._reconnect_in_progress = False
        self._reconnect_task: asyncio.Task[bool] | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{node_id}",
            update_interval=update_interval or DEFAULT_UPDATE_INTERVAL,
            config_entry=config_entry,
        )

        # Register event listener for connection errors
        config_entry.async_on_unload(
            hass.bus.async_listen(
                EVENT_CONNECTION_ERROR,
                self._handle_connection_error,
            )
        )

    @callback
    def _handle_connection_error(self, event: Event) -> None:
        """Handle connection error from ESPLocalCtrlClient.

        Args:
            event: The connection error event.
        """
        if event.data.get(CONF_NODE_ID, "") != self.node_id:
            return

        if self._reconnect_in_progress:
            return

        self._set_available(False)
        self._reconnect_attempt = 0
        self._schedule_reconnection()

    def _set_available(self, available: bool) -> None:
        """Set device availability state.

        Args:
            available: Whether the device is available.
        """
        if self._last_available == available:
            return

        self._last_available = available

        if available:
            self._consecutive_failures = 0
            self._reconnect_attempt = 0

    def _schedule_reconnection(self, delay: float = 0.5) -> None:
        """Schedule a reconnection attempt.

        Args:
            delay: Delay in seconds before attempting reconnection.
        """
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return  # Already scheduled

        self._reconnect_task = self.hass.async_create_task(
            self._delayed_reconnection(delay),
            name=f"{DOMAIN}_reconnect_{self.node_id}",
        )

    async def _delayed_reconnection(self, delay: float) -> bool:
        """Perform reconnection after delay.

        Args:
            delay: Delay in seconds before attempting.

        Returns:
            True if reconnection successful.
        """
        current_task = self._reconnect_task
        try:
            await asyncio.sleep(delay)
            return await self._attempt_single_reconnection()
        finally:
            # Only clear if no new task was scheduled during reconnection
            if self._reconnect_task is current_task:
                self._reconnect_task = None

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch data from ESP device.

        Returns:
            CoordinatorData with device state information.

        Raises:
            UpdateFailed: If device becomes unavailable after multiple failures.
        """
        try:
            is_available = await self.api.is_device_available_async(self.node_id)

            if not is_available:
                return await self._handle_device_offline()

            # Fetch properties only if discovery not yet completed
            # Discovery state is managed by API layer (single source of truth)
            if not self.api.is_discovery_completed(self.node_id):
                properties = await self._fetch_device_properties()

                if properties:
                    await self._perform_initial_discovery(properties)

            # Reset failure counter on successful update
            self._consecutive_failures = 0
            self._set_available(True)

            return CoordinatorData(
                node_id=self.node_id,
                device_name=self.device_name,
                available=True,
            )

        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except (OSError, ClientError, TimeoutError) as err:
            # Network-related errors - device may be unreachable
            self._consecutive_failures += 1

            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self._set_available(False)
                if not self._reconnect_in_progress:
                    self._schedule_reconnection()
                raise UpdateFailed(
                    f"Device {self.node_id} unreachable after "
                    f"{MAX_CONSECUTIVE_FAILURES} network failures: {err}"
                ) from err

            return CoordinatorData(
                node_id=self.node_id,
                device_name=self.device_name,
                available=self._last_available,
            )

    async def _handle_device_offline(self) -> CoordinatorData:
        """Handle device being offline.

        Marks device as unavailable and schedules reconnection in background.

        Returns:
            CoordinatorData indicating device is unavailable.
        """
        self._set_available(False)

        # Schedule reconnection in background (non-blocking)
        if not self._reconnect_in_progress:
            self._schedule_reconnection()

        return CoordinatorData(
            node_id=self.node_id,
            device_name=self.device_name,
            available=False,
        )

    async def _attempt_single_reconnection(self) -> bool:
        """Attempt a single reconnection, schedule next attempt if failed.

        Uses non-blocking exponential backoff by scheduling subsequent attempts
        instead of blocking in a while loop.

        Returns:
            True if reconnection successful, False otherwise.
        """
        if self._reconnect_in_progress:
            return False

        self._reconnect_in_progress = True

        try:
            # Check if already available (may have reconnected externally)
            if self._last_available:
                self._reconnect_attempt = 0
                return True

            device_info = self.api.devices.get(self.node_id)
            if not device_info:
                self._schedule_next_reconnection_if_needed()
                return False

            ip = device_info.get(KEY_IP)
            if not ip:
                self._schedule_next_reconnection_if_needed()
                return False

            port = device_info.get(KEY_PORT, self.api.default_port)

            # Check registry for connection state
            if await self.api.is_device_available_async(self.node_id):
                self._set_available(True)
                self._reconnect_attempt = 0
                # Refresh coordinator to update all entities' available state
                self.hass.async_create_task(
                    self._refresh_after_reconnect(),
                    name=f"{DOMAIN}_refresh_{self.node_id}",
                )
                return True

            # Try to reconnect via mDNS
            if await self.api.is_mdns_available(self.node_id, ip):
                if await self.api.register_device(self.node_id, ip, port):
                    self._set_available(True)
                    self._reconnect_attempt = 0
                    # Refresh coordinator to update all entities' available state
                    self.hass.async_create_task(
                        self._refresh_after_reconnect(),
                        name=f"{DOMAIN}_refresh_{self.node_id}",
                    )
                    return True

            # Schedule next attempt with exponential backoff
            self._schedule_next_reconnection_if_needed()

        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except (OSError, ClientError, TimeoutError) as err:
            _LOGGER.debug("Reconnection attempt for %s failed: %s", self.node_id, err)
            self._schedule_next_reconnection_if_needed()
        except Exception as err:  # noqa: BLE001 - Catch-all for unexpected reconnection errors  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "Unexpected error during reconnection for %s: %s",
                self.node_id,
                err,
                exc_info=True,
            )
            self._schedule_next_reconnection_if_needed()
        finally:
            self._reconnect_in_progress = False

        return False

    def _schedule_next_reconnection_if_needed(self) -> None:
        """Schedule next reconnection attempt if retries remain."""
        if self._reconnect_attempt < len(RECONNECT_DELAYS):
            delay = RECONNECT_DELAYS[self._reconnect_attempt]
            self._reconnect_attempt += 1
            self._schedule_reconnection(delay)
        else:
            self._reconnect_attempt = 0

    async def _refresh_after_reconnect(self) -> None:
        """Refresh coordinator after reconnection with error handling."""
        try:
            await self.async_refresh()
        except (UpdateFailed, OSError, TimeoutError) as err:
            _LOGGER.error(
                "Failed to refresh coordinator for %s after reconnection: %s",
                self.node_id,
                err,
            )

    async def _fetch_device_properties(self) -> list[dict[str, Any]] | None:
        """Fetch device properties.

        Returns:
            List of property dictionaries or None if fetch failed.
        """
        client = self.api.registry.get_client(self.node_id)
        if client is None:
            return None

        return await client.get_property_values()

    async def _perform_initial_discovery(
        self, properties: list[dict[str, Any]]
    ) -> None:
        """Perform initial entity discovery.

        Args:
            properties: List of device properties.
        """
        # Discovery state is managed by API layer (single source of truth)
        if not self.api.is_discovery_completed(self.node_id):
            await self.api.parse_and_discover_entities(
                self.node_id,
                properties,
                preferred_device_name=self.device_name,
            )
            self.api.mark_discovery_completed(self.node_id)

    @property
    def is_available(self) -> bool:
        """Return True if device is available."""
        return self._last_available

    @property
    def discovery_completed(self) -> bool:
        """Return True if initial discovery is completed."""
        return self.api.is_discovery_completed(self.node_id)

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources.

        Event listeners are automatically cleaned up via config_entry.async_on_unload().
        """
        # Cancel pending reconnection task
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        # Call parent shutdown to cancel any pending refresh
        await super().async_shutdown()
