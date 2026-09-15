# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The profile one device resolves against: a product plus the service layer.

Nothing is merged across products. The built-in service layer fills in any
service type the product does not declare itself. Entries narrowed to one
device name are held beside the type's nameless entry, so a name nothing claims
still resolves against the type.
"""

import logging

from .schema import DeviceProfile

_LOGGER = logging.getLogger(__name__)

# Profiles of one type, keyed by the device name they are scoped to. None holds
# the entry that applies to every name.
_ByName = dict[str | None, DeviceProfile]


class ProfileRegistry:
    """Lookup table of device type -> profile, plus the same for services."""

    def __init__(self, product_id: str = "") -> None:
        """Initialise an empty registry, optionally naming the product it is for."""
        self.product_id = product_id
        self._devices: dict[str, _ByName] = {}
        self._services: dict[str, _ByName] = {}
        self._sources: list[str] = []

    @classmethod
    def for_product(
        cls,
        product_id: str,
        devices: list[DeviceProfile],
        services: list[DeviceProfile],
        base_services: list[DeviceProfile] | None = None,
    ) -> "ProfileRegistry":
        """Build the registry a device bound to one product resolves against.

        ``base_services`` is the built-in service layer, consulted only for a
        service type the product did not declare.
        """
        registry = cls(product_id)
        for profile in base_services or []:
            registry._services.setdefault(profile.type, {})[profile.name] = profile
        # The product's own service blocks come after the layer, so they win.
        registry.add_profiles(product_id, devices, services)
        return registry

    @property
    def sources(self) -> list[str]:
        """Return the products and layers that contributed, in load order."""
        return list(self._sources)

    def add_profiles(
        self,
        source: str,
        devices: list[DeviceProfile],
        services: list[DeviceProfile],
    ) -> None:
        """Add one document's profiles under the given source name."""
        self._sources.append(source)
        for profile in devices:
            self._devices.setdefault(profile.type, {})[profile.name] = profile
        for profile in services:
            self._services.setdefault(profile.type, {})[profile.name] = profile

    @staticmethod
    def _pick(by_name: _ByName | None, device_name: str | None) -> DeviceProfile | None:
        """Return the entry scoped to this name, else the nameless one."""
        if not by_name:
            return None
        if device_name is not None and device_name in by_name:
            return by_name[device_name]
        return by_name.get(None)

    def get_device(
        self, device_type: str, device_name: str | None = None
    ) -> DeviceProfile | None:
        """Return the profile for a device type, or None if unknown."""
        return self._pick(self._devices.get(device_type), device_name)

    def get_service(
        self, service_type: str, device_name: str | None = None
    ) -> DeviceProfile | None:
        """Return the profile for a service type, or None if unknown."""
        return self._pick(self._services.get(service_type), device_name)

    @property
    def device_types(self) -> list[str]:
        """Return every known device type."""
        return sorted(self._devices)

    @property
    def service_types(self) -> list[str]:
        """Return every known service type."""
        return sorted(self._services)

    def describe(self) -> dict[str, object]:
        """Return a diagnostics-friendly summary of the registry."""
        return {
            "product": self.product_id,
            "sources": self.sources,
            "device_types": self.device_types,
            "service_types": self.service_types,
        }
