# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""What the device layer asks of the application hosting it.

Nothing under ``iot/`` imports Home Assistant; the event loop, the shared mDNS
instance and the report callbacks come through this one object.
``helpers/host.py`` implements it over ``hass``.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Protocol, TypeVar

from zeroconf import Zeroconf
from zeroconf.asyncio import AsyncZeroconf

_T = TypeVar("_T")


class ResolvedBinding(Protocol):
    """All this layer needs of a resolved binding: where it goes, and its name."""

    @property
    def node_id(self) -> str:
        """The node this entity belongs to."""

    @property
    def device_name(self) -> str:
        """The device instance on that node whose report drives it."""

    @property
    def platform(self) -> str:
        """The kind of entity it becomes, in the host's own vocabulary."""

    @property
    def key(self) -> str:
        """Its identity within the node, and the suffix of its unique_id."""


class IotHost(Protocol):
    """The application, as seen from the device layer."""

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The loop zeroconf's own-thread callbacks hand their work back to."""

    def create_task(self, coro: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        """Run a coroutine as a task the host keeps track of.

        Called on the loop thread only.
        """

    async def zeroconf(self) -> Zeroconf:
        """Return the shared mDNS instance."""

    async def async_zeroconf(self) -> AsyncZeroconf:
        """Return the shared mDNS instance, in its asyncio form."""

    def configured_node_ids(self) -> set[str]:
        """Return the ids of every node the user has set up.

        An announcement from any other node is not this layer's business.
        """

    def prune_entities(
        self, node_id: str, keep: set[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Remove the node's entities not among ``keep``.

        ``keep`` and the result are ``(platform, key)`` pairs.
        """

    def connection_lost(self, node_id: str) -> None:
        """Say that a node's local-control connection has just gone."""

    def address_seen(self, node_id: str, ip: str, port: int) -> None:
        """Say where a set-up node is answering from now.

        Called on every announcement, so the host can keep its record of the
        node's address current for the next start.
        """

    def binding_discovered(
        self, binding: ResolvedBinding, initial_report: dict[str, Any]
    ) -> None:
        """Announce one entity a node's report resolved to, with its current values."""

    def binding_removed(self, node_id: str, platform: str, key: str) -> None:
        """Say that an entity the node used to describe has gone."""
