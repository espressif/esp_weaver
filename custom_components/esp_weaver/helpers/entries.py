# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Config-entry lookup by node id.

Invariant set by the config flow: ``entry.unique_id`` and
``entry.data[CONF_NODE_ID]`` are the same node id string. Lookups use
``unique_id`` (Home Assistant's identity for the entry). Do not add a second
lookup that keys only on ``CONF_NODE_ID``.
"""

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from ..identity import DOMAIN

# States in which an entry is using the shared device API: set up, or in the
# middle of setting up. Not SETUP_RETRY or SETUP_ERROR, whose entries hold
# nothing, and not a disabled or unloaded entry.
_ACTIVE_STATES = frozenset(
    {ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS}
)


def entry_for_node(hass: HomeAssistant, node_id: str) -> ConfigEntry | None:
    """Return the config entry whose unique id is ``node_id``, if any."""
    return next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id == node_id
        ),
        None,
    )


def active_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return the entries that are set up or setting up.

    What the device layer may act on: an announcement from the node of an
    unloaded or disabled entry has no coordinator to reach, and opening a
    session to it would only leak one.
    """
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state in _ACTIVE_STATES
    ]
