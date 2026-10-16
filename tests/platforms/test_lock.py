# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven lock platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.lock import ESPWeaverLock
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Lock"
PARAM = "Lock State"
UNLOCKED = 0
LOCKED = 1
JAMMED = 2


def _binding(**overrides: Any) -> EntityBinding:
    """Build a lock binding whose numbers the profile names."""
    spec = AttributeSpec(
        name="state",
        param=PARAM,
        data_type="int",
        properties=("read", "write"),
        bounds={"min": 0, "max": 2, "step": 1},
        value_map={UNLOCKED: "unlocked", LOCKED: "locked", JAMMED: "jammed"},
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.lock",
        "platform": "lock",
        "name": "Lock",
        "key": "lock",
        "attributes": {
            "state": ParamBinding(device_name=DEVICE, attribute="state", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(coordinator: MagicMock, report: dict[str, Any] | None = None) -> Any:
    return ESPWeaverLock(coordinator, _binding(), TEST_DEVICE_NAME, report=report)


class TestLockReads:
    """Turning the reported number into a lock state."""

    def test_starts_unknown(self, coordinator: MagicMock) -> None:
        """Nothing is assumed before the device has reported, jammed included."""
        entity = _entity(coordinator)

        assert entity.is_locked is None
        assert entity.is_jammed is None

    def test_reads_locked(self, coordinator: MagicMock) -> None:
        """The value the profile calls locked is locked."""
        entity = _entity(coordinator, {PARAM: LOCKED})

        assert entity.is_locked is True
        assert entity.is_jammed is False

    def test_reads_unlocked(self, coordinator: MagicMock) -> None:
        """The value the profile calls unlocked is not locked."""
        entity = _entity(coordinator, {PARAM: UNLOCKED})

        assert entity.is_locked is False

    def test_reads_jammed(self, coordinator: MagicMock) -> None:
        """A jam is neither locked nor a silent failure."""
        entity = _entity(coordinator, {PARAM: JAMMED})

        assert entity.is_jammed is True
        assert entity.is_locked is False


class TestLockWrites:
    """Locking and unlocking."""

    async def test_lock_writes_the_mapped_number(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Home Assistant's lock becomes the number the device expects."""
        entity = _entity(coordinator)
        entity.hass = hass
        entity.entity_id = "lock.test"

        await entity.async_lock()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: LOCKED}}
        )
        assert entity.is_locked is True

    async def test_unlock_writes_the_mapped_number(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Unlocking is the inverse trip through the same map."""
        entity = _entity(coordinator, {PARAM: LOCKED})
        entity.hass = hass
        entity.entity_id = "lock.test"

        await entity.async_unlock()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: UNLOCKED}}
        )
        assert entity.is_locked is False
