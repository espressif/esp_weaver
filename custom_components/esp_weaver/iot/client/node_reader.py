# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""One-off read of a device that is not set up yet, for the config flow."""

import logging

from ..discovered_device import DiscoveredDevice
from ..parsers.property_parser import get_raw_config, get_raw_params
from .client import ESPLocalCtrlClient

_LOGGER = logging.getLogger(__name__)


async def read_node(device: DiscoveredDevice) -> tuple[dict, dict] | None:
    """Open a session to a discovered device, read it once, and close it.

    Returns:
        The node's ``(config, params)``, or None if the device did not answer.
    """
    client = ESPLocalCtrlClient(
        device.node_id,
        device.ip,
        port=device.port,
        pop=device.pop,
        security_mode=device.security_version or 0,
    )
    try:
        if not await client.connect():
            return None
        properties = await client.get_property_values()
    finally:
        await client.disconnect()

    if properties is None:
        _LOGGER.debug("Node %s did not answer a read", device.node_id)
        return None
    return get_raw_config(properties) or {}, get_raw_params(properties) or {}
