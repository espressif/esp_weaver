"""Test doubles shared across the suite."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.esp_weaver.iot.host import ResolvedBinding


class InlineLoop:
    """The parts of an event loop the device layer touches, run immediately.

    ``call_soon_threadsafe`` is how Zeroconf's thread hands work back to the
    loop; running the callback inline lets a test see the result at once.
    """

    def __init__(self) -> None:
        """Start with nothing scheduled."""
        self.scheduled: list[Callable[..., Any]] = []

    def call_soon_threadsafe(self, callback: Callable[..., Any], *args: Any) -> None:
        """Run the callback now, and remember that it was asked for."""
        self.scheduled.append(callback)
        callback(*args)


class FakeHost:
    """An ``IotHost`` that records what the device layer asked of it."""

    def __init__(self) -> None:
        """Start with nothing configured and nothing recorded."""
        self.loop: Any = InlineLoop()
        self.tasks: list[asyncio.Task[Any]] = []
        self.zc = MagicMock(name="zeroconf")
        self.aiozc = MagicMock(name="async_zeroconf")
        self.aiozc.zeroconf = self.zc
        self.zeroconf = AsyncMock(side_effect=lambda: self.zc)
        self.async_zeroconf = AsyncMock(side_effect=lambda: self.aiozc)
        self.configured: set[str] = set()
        # node_id -> the (platform, key) pairs registered for it
        self.entities: dict[str, set[tuple[str, str]]] = {}
        self.pruned: list[tuple[str, str]] = []
        self.lost: list[str] = []
        self.addresses: list[tuple[str, str, int]] = []
        self.discovered: list[tuple[ResolvedBinding, dict[str, Any]]] = []
        self.removed: list[tuple[str, str, str]] = []
        self.create_task: Callable[[Coroutine[Any, Any, Any]], asyncio.Task[Any]] = (
            self._create_task
        )

    def _create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Schedule the coroutine on the running loop and keep the task."""
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.append(task)
        return task

    def configured_node_ids(self) -> set[str]:
        """Return the nodes a test has declared set up."""
        return set(self.configured)

    def prune_entities(
        self, node_id: str, keep: set[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Drop the node's registered entities that are not in ``keep``."""
        registered = self.entities.setdefault(node_id, set())
        removed = sorted(registered - keep)
        registered.difference_update(removed)
        self.pruned.extend(removed)
        return removed

    def connection_lost(self, node_id: str) -> None:
        """Record the node whose connection went."""
        self.lost.append(node_id)

    def address_seen(self, node_id: str, ip: str, port: int) -> None:
        """Record where a node was announced from."""
        self.addresses.append((node_id, ip, port))

    def binding_discovered(
        self, binding: ResolvedBinding, initial_report: dict[str, Any]
    ) -> None:
        """Record an announced binding with its first report."""
        self.discovered.append((binding, initial_report))

    def binding_removed(self, node_id: str, platform: str, key: str) -> None:
        """Record an entity the node stopped describing."""
        self.removed.append((node_id, platform, key))

    def discovered_for(
        self, node_id: str
    ) -> list[tuple[ResolvedBinding, dict[str, Any]]]:
        """Return the announcements made for one node, in order."""
        return [(b, r) for b, r in self.discovered if b.node_id == node_id]


async def no_bindings(
    node_id: str, raw_config: dict[str, Any], raw_params: dict[str, Any]
) -> list[Any]:
    """A resolver with no profiles: every node announces no entities."""
    return []
