# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Entity discovery: a node's config, resolved against its profile, becomes bindings.

Each binding is announced on its own event for the platforms to build from;
nothing here knows what a light or a sensor is.
"""

from collections.abc import Awaitable, Callable, Sequence
import json
import logging
from typing import TYPE_CHECKING, Any

from ..host import IotHost, ResolvedBinding
from ..parsers.property_parser import (
    firmware_version_from_config,
    get_raw_config,
    get_raw_params,
)

if TYPE_CHECKING:
    from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)


# Turns a node's config and params into bindings; supplied by the profile layer.
BindingResolver = Callable[
    [str, dict[str, Any], dict[str, Any]],
    Awaitable[Sequence[ResolvedBinding]],
]


class DeviceDiscoveryManager:
    """Manages entity discovery for ESP IoT devices."""

    def __init__(
        self,
        host: IotHost,
        registry: "DeviceRegistry",
        resolve_bindings: BindingResolver,
    ) -> None:
        """Initialize the device discovery manager."""
        self.host = host
        self.registry = registry
        self._resolve_bindings = resolve_bindings

    async def parse_and_discover_entities(
        self, node_id: str, properties: list | None
    ) -> None:
        """Read a local node's properties and build its entities from them."""
        if not properties:
            _LOGGER.warning("No properties provided for device %s", node_id)
            return

        try:
            raw_config = get_raw_config(properties)
            raw_params = get_raw_params(properties) or {}
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            _LOGGER.exception("Failed to parse device configuration for %s", node_id)
            return
        await self.adopt_report(node_id, raw_config, raw_params)

    async def adopt_report(
        self,
        node_id: str,
        raw_config: dict[str, Any] | None,
        raw_params: dict[str, Any],
    ) -> None:
        """Keep what a node reported about itself and build its entities from it."""
        if not raw_config or "devices" not in raw_config:
            _LOGGER.warning("Device %s reported no configuration", node_id)
            return

        device = self.registry.get_device(node_id)
        if device:
            device.firmware_version = firmware_version_from_config(raw_config)
            device.raw_config = raw_config
            device.raw_params = raw_params

        await self.discover_bindings(node_id, raw_config, raw_params)

    async def discover_bindings(
        self,
        node_id: str,
        raw_config: dict[str, Any],
        raw_params: dict[str, Any],
    ) -> None:
        """Resolve the profile against a node and announce the result.

        Failures are logged and swallowed: a profile that fails to match should
        cost its entities, not the config entry.
        """
        if not raw_config:
            return

        try:
            bindings = await self._resolve_bindings(node_id, raw_config, raw_params)
        except (AttributeError, KeyError, TypeError, ValueError):
            _LOGGER.exception("Profile resolution failed for %s", node_id)
            return

        _LOGGER.debug(
            "Device %s profiles resolved %d bindings: %s",
            node_id,
            len(bindings),
            [binding.key for binding in bindings],
        )

        self._remove_stale_entities(node_id, bindings)

        for binding in bindings:
            self._fire_binding_discovered(binding, raw_params)

    def remember_report(self, node_id: str, raw_params: dict[str, Any]) -> None:
        """Keep a node's latest values, which diagnostics and the options flow read."""
        device = self.registry.get_device(node_id)
        if device is not None:
            device.raw_params = raw_params

    def _remove_stale_entities(
        self, node_id: str, bindings: Sequence[ResolvedBinding]
    ) -> None:
        """Forget entities this node's firmware no longer describes.

        After a reflash or a profile edit the old entities would otherwise stay
        in the registry, reading as available. Matched on platform as well as
        key, since moving an entity to another platform keeps its key. Skipped
        when resolution produced nothing: that is far more often a firmware
        that answered oddly than a device with no entities.
        """
        if not bindings:
            return

        keep = {(binding.platform, binding.key) for binding in bindings}
        for platform, key in self.host.prune_entities(node_id, keep):
            # The platform also holds the entity against its key; drop that
            # copy too, or an entity recreated under the same key after a
            # reflash would be taken for one that already exists and never
            # rebuilt.
            self.host.binding_removed(node_id, platform, key)

    def _fire_binding_discovered(
        self,
        binding: ResolvedBinding,
        raw_params: dict[str, Any],
    ) -> None:
        """Announce one resolved entity binding and its device's current report."""
        reported = raw_params.get(binding.device_name)
        self.host.binding_discovered(
            binding, reported if isinstance(reported, dict) else {}
        )

    async def discover_from_cloud(
        self,
        node_id: str,
        node_config: dict[str, Any],
        cloud_params: dict[str, Any],
    ) -> None:
        """Build a cloud node's entities from the config and params the cloud gave."""
        await self.adopt_report(node_id, node_config, cloud_params or {})
