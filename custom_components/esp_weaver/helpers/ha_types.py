# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Typed shapes for what the integration keeps in Home Assistant's own structures."""

from typing import TYPE_CHECKING, Literal, TypedDict

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from typing import TypeAlias

    from ..coordinator import ESPDataUpdateCoordinator

    # Written as a TypeAlias rather than a `type` statement so the runtime
    # placeholder below can stand in for it.
    ESPConfigEntry: TypeAlias = ConfigEntry[ESPDataUpdateCoordinator]  # noqa: UP040
else:
    ESPConfigEntry = ConfigEntry


class ESPConfigEntryData(TypedDict, total=False):
    """What one device's config entry stores; local and cloud fill different halves."""

    host: str
    port: int
    node_id: str
    # Proof of Possession, needed by security version 1.
    custom_pop: str
    # 0 for none, 1 for PoP, 2 for SRP6a.
    security_version: int
    connection_mode: Literal["local", "cloud"]
    rainmaker_region: Literal["global", "china"]
    # Local only: whether the mDNS record carried the device's identity.
    identity_advertised: bool


__all__ = [
    "ESPConfigEntry",
    "ESPConfigEntryData",
]
