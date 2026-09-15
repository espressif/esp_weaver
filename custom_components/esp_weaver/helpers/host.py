# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Home Assistant, as the device layer sees it: the shipped ``IotHost``."""

import asyncio
from collections.abc import Coroutine
import logging
from typing import Any, TypeVar

from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from zeroconf import Zeroconf
from zeroconf.asyncio import AsyncZeroconf

from ..identity import entity_unique_id
from ..iot.host import ResolvedBinding
from .entries import active_entries, entry_for_node
from .signals import (
    signal_binding_discovered,
    signal_binding_removed,
    signal_connection_error,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


class HomeAssistantHost:
    """Implements :class:`~..iot.host.IotHost` over a running Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Wrap the instance the device layer is to run in."""
        self._hass = hass

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The Home Assistant event loop."""
        return self._hass.loop

    def create_task(self, coro: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        """Run a coroutine as a task Home Assistant tracks and cancels on stop."""
        return self._hass.async_create_task(coro)

    async def zeroconf(self) -> Zeroconf:
        """Return the Zeroconf instance Home Assistant shares between integrations."""
        return await zeroconf.async_get_instance(self._hass)

    async def async_zeroconf(self) -> AsyncZeroconf:
        """Return the shared Zeroconf instance in its asyncio form."""
        return await zeroconf.async_get_async_instance(self._hass)

    def configured_node_ids(self) -> set[str]:
        """Return the node id of every entry that is set up or setting up.

        Not an unloaded or disabled entry's: mDNS would otherwise open a
        session to a node whose coordinator is gone and hold it until the
        last entry unloads.
        """
        return {
            entry.unique_id for entry in active_entries(self._hass) if entry.unique_id
        }

    @callback
    def prune_entities(
        self, node_id: str, keep: set[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Remove the node's registered entities that are not among ``keep``.

        Scoped to unique_ids this integration built for the node.
        """
        config_entry = entry_for_node(self._hass, node_id)
        if config_entry is None:
            return []

        prefix = entity_unique_id(node_id, "")
        wanted = {(platform, entity_unique_id(node_id, key)) for platform, key in keep}
        removed: list[tuple[str, str]] = []

        registry = er.async_get(self._hass)
        for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
            if not entry.unique_id.startswith(prefix):
                continue
            if (entry.domain, entry.unique_id) in wanted:
                continue
            _LOGGER.info(
                "Removing %s: node %s no longer reports the parameters it needs",
                entry.entity_id,
                node_id,
            )
            registry.async_remove(entry.entity_id)
            removed.append((entry.domain, entry.unique_id.removeprefix(prefix)))

        return removed

    @callback
    def connection_lost(self, node_id: str) -> None:
        """Tell the node's coordinator that its client has lost the connection."""
        async_dispatcher_send(self._hass, signal_connection_error(node_id))

    @callback
    def address_seen(self, node_id: str, ip: str, port: int) -> None:
        """Write the node's current address into its entry, if it moved.

        The entry is what the next start connects to first; left at the old
        address, that start would fail once and wait for mDNS. Only the
        address changes, so the update listener sees no binding change and
        does not reload the entry.
        """
        entry = entry_for_node(self._hass, node_id)
        if entry is None or (entry.data.get(CONF_HOST), entry.data.get(CONF_PORT)) == (
            ip,
            port,
        ):
            return
        _LOGGER.info(
            "Node %s moved to %s:%s; updating its entry from %s:%s",
            node_id,
            ip,
            port,
            entry.data.get(CONF_HOST),
            entry.data.get(CONF_PORT),
        )
        self._hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HOST: ip, CONF_PORT: port}
        )

    @callback
    def binding_discovered(
        self, binding: ResolvedBinding, initial_report: dict[str, Any]
    ) -> None:
        """Hand a resolved binding to the platform that builds entities for it."""
        async_dispatcher_send(
            self._hass,
            signal_binding_discovered(binding.node_id),
            binding,
            initial_report,
        )

    @callback
    def binding_removed(self, node_id: str, platform: str, key: str) -> None:
        """Tell the platform that built an entity that it has gone."""
        async_dispatcher_send(
            self._hass, signal_binding_removed(node_id), platform, key
        )
