# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Resolved links between one Home Assistant entity and its device parameters.

A binding is a profile entry matched against one node's config: every attribute
exists on the device, bounds and permissions are the firmware's, and the device
instance name is filled in. Nothing downstream needs to know about YAML.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .schema import AlertSpec, AttributeSpec, AutoResetSpec
from .transform import clamp_to_bounds, device_to_ha, ha_to_device


@dataclass(frozen=True, slots=True)
class ThresholdRole:
    """What a number entity bounds: which sensor, which end, and its partner.

    ``partner_key`` is the number holding the other end, or None.
    """

    sensor_key: str
    end: str
    partner_key: str | None = None


@dataclass(frozen=True, slots=True)
class ParamBinding:
    """One HA attribute wired to one parameter of one device instance."""

    device_name: str
    attribute: str
    spec: AttributeSpec

    @property
    def param(self) -> str:
        """Return the parameter name as the firmware reports it."""
        return self.spec.param

    @property
    def writable(self) -> bool:
        """Return True if this parameter accepts writes."""
        return self.spec.writable

    @property
    def ha_options(self) -> tuple[str, ...] | None:
        """Return the values this attribute can take, as Home Assistant sees them.

        A ``map`` wins over the enumerated values.
        """
        if self.spec.value_map:
            return tuple(str(value) for value in self.spec.value_map.values())
        if self.spec.options:
            return tuple(self.spec.options)
        return None

    @property
    def span(self) -> tuple[float, float] | None:
        """Return the range this parameter accepts, or None if an end is unknown."""
        bounds = self.spec.bounds
        if not bounds or "min" not in bounds or "max" not in bounds:
            return None
        return float(bounds["min"]), float(bounds["max"])

    def to_ha(self, value: Any) -> Any:
        """Convert a reported device value into its HA representation."""
        return device_to_ha(self.spec, value)

    def to_device(self, value: Any) -> Any:
        """Convert an HA value into the device encoding, clamped to bounds."""
        return clamp_to_bounds(self.spec, ha_to_device(self.spec, value))


@dataclass(frozen=True, slots=True)
class EntityBinding:
    """Everything needed to create and drive one Home Assistant entity."""

    node_id: str
    device_name: str
    device_type: str
    platform: str
    name: str
    key: str
    icon: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    unit: str | None = None
    display_precision: int | None = None
    options: tuple[str, ...] | None = None
    attributes: dict[str, ParamBinding] = field(default_factory=dict)
    extra_attributes: dict[str, ParamBinding] = field(default_factory=dict)
    auto_reset: AutoResetSpec | None = None
    alert: AlertSpec | None = None
    # On a sensor: the keys of the numbers bounding it, by end ("min"/"max").
    # Only ends whose number was actually resolved on this node appear.
    thresholds: Mapping[str, str] = field(default_factory=dict)
    # On a number: the sensor it bounds, if the profile declares one.
    threshold: ThresholdRole | None = None

    @property
    def primary(self) -> ParamBinding | None:
        """Return the binding that carries the entity state."""
        return primary_attribute(self.attributes)

    @property
    def writable(self) -> bool:
        """Return True if the entity's primary parameter accepts writes."""
        primary = self.primary
        return primary is not None and primary.writable

    def command(self, value: Any, attribute: str | None = None) -> dict[str, Any]:
        """Build the ``{device_name: {param: value}}`` payload that writes a value.

        ``{}`` if the attribute is unknown or read-only.
        """
        binding = (
            self.attributes.get(attribute) if attribute is not None else self.primary
        )
        if binding is None or not binding.writable:
            return {}
        return {binding.device_name: {binding.param: binding.to_device(value)}}

    def commands(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Build one payload writing several attributes at once.

        Unknown or read-only attributes are dropped; empty means nothing could
        be written.
        """
        payload: dict[str, dict[str, Any]] = {}
        for attribute, value in values.items():
            for device_name, params in self.command(value, attribute).items():
                payload.setdefault(device_name, {}).update(params)
        return payload


def primary_attribute(
    attributes: Mapping[str, ParamBinding],
) -> ParamBinding | None:
    """Return the attribute that carries an entity's state; shared with the resolver."""
    for candidate in ("state", "value"):
        if candidate in attributes:
            return attributes[candidate]
    return next(iter(attributes.values()), None)
