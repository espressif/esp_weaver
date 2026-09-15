# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Dispatcher signals the integration uses to talk to itself.

Over ``homeassistant.helpers.dispatcher``, not the event bus: a report every
poll and a binding per entity are neither JSON nor anything the recorder should
keep. Every signal is scoped to one node. The device layer reports to an
:class:`~..iot.host.IotHost`; :mod:`.host` turns that into these signals.
"""

from typing import Final

from ..identity import DOMAIN

__all__ = [
    "SIGNAL_BINDING_DISCOVERED",
    "SIGNAL_BINDING_REMOVED",
    "SIGNAL_CONNECTION_ERROR",
    "SIGNAL_PARAMS_UPDATE",
    "signal_binding_discovered",
    "signal_binding_removed",
    "signal_connection_error",
    "signal_params_update",
]

# Sent with no arguments when a node's local-control client has lost its
# connection. The coordinator answers it by scheduling a reconnection.
SIGNAL_CONNECTION_ERROR: Final = f"{DOMAIN}_connection_error"

# Sent as ``(binding, initial_report)``: the resolved EntityBinding and the
# device instance's current report, so the entity starts with known values.
SIGNAL_BINDING_DISCOVERED: Final = f"{DOMAIN}_binding_discovered"

# Sent as ``(platform, key)`` for an entity the node has stopped describing.
SIGNAL_BINDING_REMOVED: Final = f"{DOMAIN}_binding_removed"

# Sent as ``(params,)``: a node's report exactly as it arrived, keyed by device
# instance name. Every entity picks out the parameters its own binding names.
SIGNAL_PARAMS_UPDATE: Final = f"{DOMAIN}_params_update"


def signal_connection_error(node_id: str) -> str:
    """Return the connection-error signal for one node."""
    return f"{SIGNAL_CONNECTION_ERROR}_{node_id}"


def signal_binding_discovered(node_id: str) -> str:
    """Return the binding-discovered signal for one node."""
    return f"{SIGNAL_BINDING_DISCOVERED}_{node_id}"


def signal_binding_removed(node_id: str) -> str:
    """Return the binding-removed signal for one node."""
    return f"{SIGNAL_BINDING_REMOVED}_{node_id}"


def signal_params_update(node_id: str) -> str:
    """Return the params-update signal for one node."""
    return f"{SIGNAL_PARAMS_UPDATE}_{node_id}"
