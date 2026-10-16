# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Field key constants for ESP-Weaver data dictionaries."""

from typing import Final

# Network and connection
KEY_IP: Final[str] = "ip"
KEY_PORT: Final[str] = "port"
KEY_NODE_ID: Final[str] = "node_id"
KEY_REGISTERED: Final[str] = "registered"

# Device info
KEY_FIRMWARE_VERSION: Final[str] = "firmware_version"
KEY_LAST_SUCCESS: Final[str] = "last_success"

# Data payload
KEY_PROPERTIES: Final[str] = "properties"
KEY_VALUE: Final[str] = "value"
KEY_PARAMS: Final[str] = "params"
KEY_NAME: Final[str] = "name"
KEY_MAC: Final[str] = "mac"
KEY_POP: Final[str] = "pop"
# What a device answered about its own security, read back in the config flow.
KEY_POP_REQUIRED: Final[str] = "pop_required"

# ESP protocol keys
KEY_INFO: Final[str] = "info"
KEY_DEVICES: Final[str] = "devices"
KEY_TYPE: Final[str] = "type"
# The parameter type that carries a device's name, ``Name`` in the standard set.
PARAM_TYPE_NAME: Final[str] = "esp.param.name"
KEY_COUNT: Final[str] = "count"
KEY_MODEL: Final[str] = "model"
KEY_HW_VERSION: Final[str] = "hw_version"
KEY_FW_VERSION: Final[str] = "fw_version"
KEY_SW_VERSION: Final[str] = "sw_version"
KEY_IDENTIFIERS: Final[str] = "identifiers"
KEY_MANUFACTURER: Final[str] = "manufacturer"

# Security-info keys, as the security validator reports them
KEY_SECURITY_VERSION: Final[str] = "security_version"

# How a device is reached
CONNECTION_MODE_LOCAL: Final = "local"
CONNECTION_MODE_CLOUD: Final = "cloud"

# RainMaker region values. Bare Final on purpose: these are also the members of
# the Literal the config entry and the CLI client are typed with.
RAINMAKER_REGION_GLOBAL: Final = "global"
RAINMAKER_REGION_CHINA: Final = "china"
