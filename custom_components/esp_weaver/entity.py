# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver base entity class."""

from typing import TYPE_CHECKING

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .iot.specs.device_specs import DEFAULT_MANUFACTURER
from .iot.specs.events import DOMAIN

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator


class ESPWeaverBaseEntity(CoordinatorEntity["ESPDataUpdateCoordinator"]):
    """Base entity class for ESP-Weaver devices.

    Provides common functionality for all ESP-Weaver entities including:
    - Automatic unique_id generation from node_id and entity_key
    - Device info generation for device registry
    - Availability based on coordinator state
    """

    _node_id: str
    _device_name: str
    _attr_has_entity_name: bool = True
    _attr_should_poll: bool = False

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        entity_key: str | None = None,
        device_name: str | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self._node_id = node_id
        self._device_name = (
            device_name if device_name is not None else coordinator.device_name
        )

        # Auto-generate unique_id if entity_key is provided
        if entity_key:
            self._attr_unique_id = f"{DOMAIN}_{node_id}_{entity_key}"

        # Auto-generate device_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            name=self._device_name,
            manufacturer=DEFAULT_MANUFACTURER,
            model=model,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.is_available


__all__ = [
    "ESPWeaverBaseEntity",
]
