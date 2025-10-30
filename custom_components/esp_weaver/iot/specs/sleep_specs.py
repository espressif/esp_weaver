# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Low power and sleep specifications and constants."""

from typing import Final

# Sleep State Constants
SLEEP_STATE_AWAKE: Final[str] = "awake"
SLEEP_STATE_LIGHT_SLEEP: Final[str] = "light_sleep"
SLEEP_STATE_DEEP_SLEEP: Final[str] = "deep_sleep"
SLEEP_STATE_HIBERNATION: Final[str] = "hibernation"

# All valid sleep states as a tuple (for entity _attr_options and validation)
SLEEP_STATE_OPTIONS: Final[tuple[str, ...]] = (
    SLEEP_STATE_AWAKE,
    SLEEP_STATE_LIGHT_SLEEP,
    SLEEP_STATE_DEEP_SLEEP,
    SLEEP_STATE_HIBERNATION,
)

# Sleep state icon mapping
SLEEP_ICON_MAPPING: Final[dict[str, str]] = {
    SLEEP_STATE_AWAKE: "mdi:eye",
    SLEEP_STATE_LIGHT_SLEEP: "mdi:sleep",
    SLEEP_STATE_DEEP_SLEEP: "mdi:power-sleep",
    SLEEP_STATE_HIBERNATION: "mdi:snowflake",
}
