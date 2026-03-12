# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Home Assistant specific type definitions for ESP-Weaver integration.

This module provides type definitions that depend on Home Assistant.
Pure Python types are in iot.entity_states.

Type Definitions:
- ESPConfigEntryData: Configuration entry data structure
- ESPConfigEntry: Type alias for config entry with runtime data
- CoordinatorData: Data returned by the coordinator
- DeviceProperty: Type for individual device property entries
"""

from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from typing import TypeAlias

    from ..coordinator import ESPDataUpdateCoordinator

    # Type alias for ESP config entry with runtime data
    # Using TypeAlias for TYPE_CHECKING block compatibility with runtime placeholder
    ESPConfigEntry: TypeAlias = ConfigEntry[ESPDataUpdateCoordinator]  # noqa: UP040
else:
    # Runtime placeholder - actual type checking happens at TYPE_CHECKING time
    ESPConfigEntry = ConfigEntry


# Config Entry Types


class ESPConfigEntryData(TypedDict, total=False):
    """TypedDict for config entry data. All fields are optional.

    Attributes:
        host: Device IP address.
        port: Device port number.
        node_id: Unique device identifier.
        custom_pop: Proof of Possession for security authentication.
        security_version: Security protocol version (0=none, 1=PoP required, 2=SRP6a).
    """

    host: str
    port: int
    node_id: str
    custom_pop: str
    security_version: int


# Device Property Types


class DeviceProperty(TypedDict, total=False):
    """TypedDict for individual device property entries.

    Represents a single property returned from ESP device property queries.
    All fields are optional as not all properties include all keys.

    Attributes:
        name: Property name identifier (e.g., "config", "params").
        value: Property value as bytes, string, or dict.
        type: Property type identifier (integer).
        flags: Property flags (integer).
    """

    name: str
    value: bytes | str | dict[str, Any] | None
    type: int
    flags: int


# Coordinator Data Types


class CoordinatorData(TypedDict, total=False):
    """TypedDict for coordinator data. All fields are optional.

    Attributes:
        node_id: Unique device identifier.
        device_name: Human-readable device name.
        available: Whether the device is currently available.
        properties: List of device properties with key-value pairs.
        last_update: Timestamp of the last successful update.
    """

    node_id: str
    device_name: str
    available: bool
    properties: list[DeviceProperty]
    last_update: float


__all__ = [
    "CoordinatorData",
    "DeviceProperty",
    "ESPConfigEntry",
    "ESPConfigEntryData",
]
