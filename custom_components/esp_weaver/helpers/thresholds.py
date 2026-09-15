# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reading the threshold numbers a profile pairs with a sensor.

A threshold is a number entity of the same node, found by its unique_id and
read from the state machine as displayed, so that sensor and number compare in
the user's units.
"""

from typing import Any

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..identity import DOMAIN, entity_unique_id


def as_float(value: Any) -> float | None:
    """Return a value as a float, or None if it is not numeric.

    Booleans are not numbers here, and neither are the ``unknown`` and
    ``unavailable`` states.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def threshold_entity_id(hass: HomeAssistant, node_id: str, key: str) -> str | None:
    """Return the entity id of one threshold number, found by unique_id."""
    return er.async_get(hass).async_get_entity_id(
        NUMBER_DOMAIN, DOMAIN, entity_unique_id(node_id, key)
    )


def threshold_value(hass: HomeAssistant, node_id: str, key: str) -> float | None:
    """Read one threshold number's displayed value, or None if it has none yet."""
    entity_id = threshold_entity_id(hass, node_id, key)
    state = hass.states.get(entity_id) if entity_id else None
    return as_float(state.state) if state else None
