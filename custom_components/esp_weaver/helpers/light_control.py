# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Light control event handlers for ESP-Weaver integration.

This module provides Home Assistant-specific light control functionality:
- Event listener setup for light property changes
- Integration with HA event bus

For pure Python light utilities (conversion, parsing), use:
    from iot.utils.light_utils import ...
"""

from collections.abc import Callable
import logging
from typing import Any, Protocol

from homeassistant.core import Event, HomeAssistant

from ..iot.specs.events import EVENT_LIGHT_SET_PROPERTIES
from ..iot.specs.keys import CONF_NODE_ID, KEY_PROPERTIES

_LOGGER = logging.getLogger(__name__)


class ESPAPIProtocol(Protocol):
    """Protocol for ESP API interface used by light control."""

    async def set_local_ctrl_property(
        self, node_id: str, prop_name: str, value: Any
    ) -> bool:
        """Set a local control property on the device."""
        ...


def setup_light_control_listener(
    hass: HomeAssistant,
    node_id: str,
    api: ESPAPIProtocol,
) -> Callable[[], None]:
    """Set up light control event listener.

    This function registers a listener for light property changes from HA UI.
    When a light entity fires `EVENT_LIGHT_SET_PROPERTIES` event,
    this handler calls the API to send the command to the device.

    Args:
        hass: Home Assistant instance.
        node_id: Device node ID.
        api: ESP API instance for device communication.

    Returns:
        Callable to unsubscribe the event listener.
    """

    async def handle_light_set_properties(event: Event) -> None:
        """Handle light property set events from HA UI."""
        event_node_id = event.data.get(CONF_NODE_ID, "")
        if event_node_id != node_id:
            return

        properties = event.data.get(KEY_PROPERTIES, {})
        if not properties:
            return

        # Send each property to the device
        for prop_name, value in properties.items():
            try:
                success = await api.set_local_ctrl_property(
                    event_node_id, prop_name, value
                )
                if not success:
                    _LOGGER.warning(
                        "Failed to set light property: %s=%s for %s",
                        prop_name,
                        value,
                        event_node_id,
                    )
            except (OSError, TimeoutError) as err:
                # OSError: network-level errors (includes ConnectionError)
                # TimeoutError: API call timeout
                _LOGGER.warning(
                    "Error setting light property %s=%s for %s: %s",
                    prop_name,
                    value,
                    event_node_id,
                    err,
                )

    return hass.bus.async_listen(
        EVENT_LIGHT_SET_PROPERTIES, handle_light_set_properties
    )
