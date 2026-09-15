# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Everything the integration keeps in ``hass.data``, as one typed object."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback

from .identity import DOMAIN

if TYPE_CHECKING:
    from .iot.client.device_api import ESPWeaverApi
    from .iot.cloud import RainMakerCLIClient
    from .profiles.catalog import ProfileCatalog
    from .profiles.mirror import RepositoryMirror
    from .profiles.registry import ProfileRegistry
    from .profiles.schema import DeviceProfile
    from .profiles.store import ProfileStore


@dataclass
class DomainData:
    """What the integration holds for the lifetime of a run.

    The device API and the profile caches are shared by every entry, read on
    first use, and dropped when the last entry unloads.
    """

    api: ESPWeaverApi | None = None
    # One RainMaker CLI client per region, shared by the cloud entries in it.
    cloud_clients: dict[str, RainMakerCLIClient] = field(default_factory=dict)
    # The profile binding each loaded entry was set up with, so that an
    # options change can tell whether it was the binding that changed.
    loaded_bindings: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    catalog: ProfileCatalog | None = None
    catalog_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    service_layer: list[DeviceProfile] | None = None
    store: ProfileStore | None = None
    mirror: RepositoryMirror | None = None
    # Whether this run has started refreshing the repository copy in the
    # background yet; done once, by the first entry to set up.
    repository_refresh_started: bool = False
    # Registries already built, by (product id, content hash). Content never
    # changes under a hash, so these never go stale.
    registries: dict[tuple[str, str], ProfileRegistry] = field(default_factory=dict)


@callback
def domain_data(hass: HomeAssistant) -> DomainData:
    """Return the integration's data, creating it on first use."""
    data: DomainData | None = hass.data.get(DOMAIN)
    if data is None:
        data = hass.data[DOMAIN] = DomainData()
    return data


@callback
def drop_domain_data(hass: HomeAssistant) -> None:
    """Forget everything kept for this run."""
    hass.data.pop(DOMAIN, None)
