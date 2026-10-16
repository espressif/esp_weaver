# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Hands a device's report to the entities built for it."""

from collections.abc import Callable
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .signals import signal_params_update


def create_event_dispatcher(
    hass: HomeAssistant,
) -> Callable[[str, dict[str, Any]], None]:
    """Bind the dispatcher to one Home Assistant, ready to be injected into the api."""
    return partial(fire_property_events, hass)


@callback
def fire_property_events(
    hass: HomeAssistant,
    node_id: str,
    params_data: dict[str, Any],
) -> None:
    """Forward a device's report, as sent, to the entities built from its profile.

    Over the dispatcher, not the event bus: a report every poll for every node
    would otherwise reach the recorder and every websocket client.
    """
    if not params_data:
        return

    async_dispatcher_send(hass, signal_params_update(node_id), params_data)
