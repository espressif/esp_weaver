# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""A device the config flow found, before it is anything to Home Assistant.

Not to be confused with DeviceInfo in managers/device_registry.py, which
tracks a device that is already set up.
"""

from dataclasses import dataclass

from .specs.device_specs import DEFAULT_PORT


@dataclass(slots=True)
class DiscoveredDevice:
    """One device as mDNS and security detection described it."""

    ip: str
    node_id: str
    port: int = DEFAULT_PORT
    device_name: str | None = None
    # 0 = no authentication, 1 = PoP, 2 = SRP6a. None until detection has run.
    security_version: int | None = None
    # The proof of possession the user typed, once it has opened a session.
    pop: str | None = None
    # The model the device advertised in the TXT record; None where the
    # firmware (older) advertised nothing.
    model: str | None = None
