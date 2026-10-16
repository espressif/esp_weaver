# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver lock entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.lock import LockEntity, LockState
from homeassistant.const import Platform

from .entity import ESPWeaverBindingEntity
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1


class ESPWeaverLock(ESPWeaverBindingEntity, LockEntity):
    """A lock whose parameter mapping comes from a device profile."""

    def _configure(self) -> None:
        """Set up the lock."""
        self._state: str | None = None

    @property
    def is_locked(self) -> bool | None:
        """Return True if locked, or None until the device has said."""
        if self._state is None:
            return None
        return self._state == LockState.LOCKED

    @property
    def is_jammed(self) -> bool | None:
        """Return True if the mechanism reported that it is stuck; None until heard from."""
        if self._state is None:
            return None
        return self._state == LockState.JAMMED

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the lock state the device reported."""
        state = self._read_primary(reported)
        if state is None:
            return False

        state = str(state)
        if state == self._state:
            return False

        self._state = state
        return True

    async def _set(self, state: str) -> None:
        """Send a new state to the device and adopt it optimistically."""
        self._state = state
        await self._write_optimistic(state)

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device."""
        await self._set(LockState.LOCKED)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the device."""
        await self._set(LockState.UNLOCKED)


async_setup_entry = binding_platform_setup(Platform.LOCK, ESPWeaverLock)
