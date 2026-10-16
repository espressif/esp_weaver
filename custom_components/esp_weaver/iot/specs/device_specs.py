# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device-related specifications and constants."""

from typing import Final

# Network connection defaults
DEFAULT_PORT: Final[int] = 8080
DEFAULT_SECURITY_MODE: Final[int] = 1  # 0=no security, 1=PoP, 2=SRP6a

# ESP-RainMaker property indices: the object model first, the values second.
PARAMS_PROPERTY_INDEX: Final[int] = 1

# Timeouts, in seconds
PROPERTY_SET_TIMEOUT: Final[float] = 5.0
SESSION_CLEANUP_DELAY: Final[float] = 0.1

# TCP keepalive
TCP_KEEPIDLE: Final[int] = 10
TCP_KEEPINTVL: Final[int] = 5
TCP_KEEPCNT: Final[int] = 3

CONFIG_PROPERTY_NAMES: Final[tuple[str, ...]] = (
    "config",
    "device_config",
    "configuration",
    "setup",
    "info",
)
