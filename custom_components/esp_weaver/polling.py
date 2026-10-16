# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""What one poll of a node consists of, per connection mode.

The coordinator decides *when* to poll and what a failure means; one class per
mode here keeps it free of ``if is_cloud`` branches.
"""

import logging
from typing import TYPE_CHECKING, Protocol

from aiohttp import ClientError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CLOUD_CONNECTIVITY_CHECK_POLLS,
    CLOUD_DISCOVERY_FAILURE_THRESHOLD,
    CLOUD_FIRMWARE_CHECK_POLLS,
    CLOUD_PARAMS_FAILURE_THRESHOLD,
)
from .iot.parsers.property_parser import (
    firmware_version_from_config,
    firmware_version_from_properties,
    get_raw_params,
)

if TYPE_CHECKING:
    from .iot.client.device_api import ESPWeaverApi

_LOGGER = logging.getLogger(__name__)


class DevicePoller(Protocol):
    """One connection mode's answer to each step of a poll."""

    # Whether the coordinator can reconnect to the node itself (local) or has
    # to wait for the next poll (cloud).
    reconnects: bool

    async def is_reachable(self) -> bool:
        """Say whether the node can be asked at all right now."""

    async def discover(self) -> None:
        """Read what the node is made of and mark discovery complete if it answered."""

    def discovery_pending(self) -> None:
        """Note a poll that could not discover the node yet.

        Raises:
            UpdateFailed: If that has gone on long enough to be a fault.
        """

    async def poll(self) -> bool:
        """Read the node's state and publish it.

        Returns:
            False if the node could not be asked, so it stops counting as available.
        """

    def polled_ok(self) -> None:
        """Note a poll that succeeded, resetting whatever failure counts there are."""


class LocalPoller:
    """Polls a node over ESP Local Control."""

    reconnects = True

    def __init__(self, api: "ESPWeaverApi", node_id: str) -> None:
        """Bind to one node."""
        self._api = api
        self._node_id = node_id

    async def is_reachable(self) -> bool:
        """A local node is reachable while its session is up."""
        return await self._api.is_device_available_async(self._node_id)

    async def discover(self) -> None:
        """Fetch properties from the device and perform entity discovery."""
        properties = await self._api.read_properties(self._node_id)
        if not properties:
            return

        await self._api.parse_and_discover_entities(self._node_id, properties)
        self._api.mark_discovery_completed(self._node_id)

    def discovery_pending(self) -> None:
        """A local node that has not answered is handled as unreachable, not here."""

    async def poll(self) -> bool:
        """Read the device's parameters and fire update events.

        RainMaker firmware only answers reads over local control; its updates
        go to the cloud over MQTT and never reach Home Assistant.
        """
        properties = await self._api.read_properties(self._node_id)
        if properties is None:
            return False

        # The read carries the config too; taking the version from it is how
        # the coordinator notices an OTA.
        if version := firmware_version_from_properties(properties):
            self._api.set_firmware_version(self._node_id, version)

        params = get_raw_params(properties)
        if not params:
            _LOGGER.debug("No params read back from local device %s", self._node_id)
            return True

        await self._api.publish_params(self._node_id, params)
        return True

    def polled_ok(self) -> None:
        """Nothing is counted for a local node beyond what the coordinator counts."""


class CloudPoller:
    """Polls a node through the RainMaker cloud."""

    reconnects = False

    def __init__(self, api: "ESPWeaverApi", node_id: str) -> None:
        """Bind to one node, with every count at zero."""
        self._api = api
        self._node_id = node_id

        self._discovery_failures = 0
        self._params_failures = 0
        self._poll_count = 0
        self._firmware_poll_count = 0
        self._offline = False

    async def is_reachable(self) -> bool:
        """The cloud can always be asked; whether the node is online is its answer."""
        return True

    async def discover(self) -> None:
        """Fetch config from the cloud and perform entity discovery."""
        node_config = await self._api.get_cloud_node_config(self._node_id)
        if not node_config or "devices" not in node_config:
            _LOGGER.debug("Cloud discovery: no config for %s yet", self._node_id)
            return

        cloud_params = await self._api.get_cloud_params(self._node_id)
        await self._api.discover_from_cloud(
            self._node_id, node_config, cloud_params or {}
        )
        self._api.mark_discovery_completed(self._node_id)

    def discovery_pending(self) -> None:
        """Report a node that is registered but has not said what it is.

        Raises:
            UpdateFailed: Once the wait has gone on too long to be the cloud
                catching up.
        """
        self._discovery_failures += 1
        if self._discovery_failures >= CLOUD_DISCOVERY_FAILURE_THRESHOLD:
            raise UpdateFailed(
                f"Cloud device {self._node_id}: config not available "
                f"after {self._discovery_failures} attempts"
            )

        _LOGGER.debug(
            "Cloud discovery pending for %s (%d/%d)",
            self._node_id,
            self._discovery_failures,
            CLOUD_DISCOVERY_FAILURE_THRESHOLD,
        )

    async def poll(self) -> bool:
        """Poll the node's state and fire update events.

        Connectivity, a separate cloud call, is asked every few polls, or every
        poll while offline.
        """
        self._poll_count += 1
        if self._offline or self._poll_count >= CLOUD_CONNECTIVITY_CHECK_POLLS:
            self._poll_count = 0
            if not await self._check_connectivity():
                return False

        params = await self._api.get_cloud_params(self._node_id)
        if not params:
            self._params_failures += 1
            if self._params_failures >= CLOUD_PARAMS_FAILURE_THRESHOLD:
                _LOGGER.warning(
                    "Cloud device %s: %d consecutive param failures, "
                    "marking unavailable",
                    self._node_id,
                    self._params_failures,
                )
                return False
            _LOGGER.debug(
                "No cloud params for %s (%d/%d)",
                self._node_id,
                self._params_failures,
                CLOUD_PARAMS_FAILURE_THRESHOLD,
            )
            return True

        self._params_failures = 0
        await self._api.publish_params(self._node_id, params)
        await self._refresh_firmware_version()
        return True

    def polled_ok(self) -> None:
        """A successful poll means the node's config has been read."""
        self._discovery_failures = 0

    async def _refresh_firmware_version(self) -> None:
        """Re-read the node's config now and then, for the firmware version an OTA changes."""
        self._firmware_poll_count += 1
        if self._firmware_poll_count < CLOUD_FIRMWARE_CHECK_POLLS:
            return
        self._firmware_poll_count = 0

        try:
            node_config = await self._api.get_cloud_node_config(self._node_id)
        except (OSError, ClientError) as err:
            # The entities have their params already, so this failing is not
            # worth failing the poll over - the next read is due either way.
            _LOGGER.debug("Could not re-read config of %s: %s", self._node_id, err)
            return

        if version := firmware_version_from_config(node_config):
            self._api.set_firmware_version(self._node_id, version)

    async def _check_connectivity(self) -> bool:
        """Ask the cloud whether the node is connected to it."""
        connectivity = await self._api.is_cloud_device_online(self._node_id)

        if connectivity is None:
            # Missing from the account, an expired login, an unreadable answer:
            # the cloud reports all three alike, so none of them can be named.
            _LOGGER.warning(
                "Could not read the cloud state of device %s", self._node_id
            )
            self._offline = True
            return False

        if not connectivity:
            if not self._offline:
                _LOGGER.info("Cloud device %s is offline", self._node_id)
            self._offline = True
            return False

        if self._offline:
            _LOGGER.info("Cloud device %s is back online", self._node_id)
        self._offline = False
        self._params_failures = 0
        return True
