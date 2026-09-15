# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Integration-wide constants and tuning knobs.

A name only one module uses lives in that module. IoT-layer constants (KEY_*,
RAINMAKER_*) come from ``iot.specs``.
"""

from typing import Final

from homeassistant.const import Platform

from .identity import DOMAIN

# The one event the integration fires: a sensor alert beginning or ending.
# docs/device_profiles_in_detail.md lists the fields.
EVENT_ALERT: Final = f"{DOMAIN}_alert"
# `kind` in the event data: a level the profile declares on an attribute, or
# a reading past a threshold number the user set.
ALERT_KIND_LEVEL: Final = "level"
ALERT_KIND_THRESHOLD: Final = "threshold"
# `end` in a threshold event: which end of the user's range a reading went
# past. Also names the translation string the message is worded from.
THRESHOLD_ALERT_HIGH: Final = "high"
THRESHOLD_ALERT_LOW: Final = "low"

PLATFORMS: Final[tuple[Platform, ...]] = (
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.DATE,
    Platform.DATETIME,
    Platform.EVENT,
    Platform.FAN,
    Platform.HUMIDIFIER,
    Platform.LAWN_MOWER,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.REMOTE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
    Platform.VACUUM,
    Platform.VALVE,
    Platform.WATER_HEATER,
)

# Config entry keys
CONF_NODE_ID: Final = "node_id"
CONF_CUSTOM_POP: Final = "custom_pop"
CONF_SECURITY_VERSION: Final = "security_version"
CONF_CONNECTION_MODE: Final = "connection_mode"
CONF_RAINMAKER_REGION: Final = "rainmaker_region"
# Whether the device's mDNS record said what it is; False for older firmware,
# whose model is then not held against the product (profiles/fit.py).
CONF_IDENTITY_ADVERTISED: Final = "identity_advertised"

# Whether the config flow offers cloud control next to local control.
# Off: cloud control is not supported in this release. New entries are
# local-only; the cloud modules stay in the tree but are not offered in the UI
# and bring no dependency into the manifest.
CLOUD_CONTROL_ENABLED: Final[bool] = False

# Whether the first step of the config flow offers the field that reads the
# profile repository from a pull request. Off: the field is not shown and the
# step neither reads nor changes what the repository copy follows. The
# mechanism itself (``RepositoryRef``, the mirror, the vendor step's note)
# stays in the tree, to be offered again when it is needed.
REPOSITORY_SOURCE_FIELD_ENABLED: Final[bool] = False

# Timeouts, polling and retry
DEVICE_SETUP_TIMEOUT: Final = 15
DEFAULT_UPDATE_INTERVAL_SECONDS: Final = 60
RECONNECT_DELAYS: Final[tuple[int, ...]] = (1, 2, 4, 8, 16, 30)
# How often, at most, a write that found no parameter to address has the
# node's structure read again.
REDISCOVERY_MIN_INTERVAL_SECONDS: Final = 60

# Cloud device availability
CLOUD_CONNECTIVITY_CHECK_POLLS: Final = 5  # Check connectivity every 5th poll (~5 min)
CLOUD_PARAMS_FAILURE_THRESHOLD: Final = 3
CLOUD_DISCOVERY_FAILURE_THRESHOLD: Final = 3

# A cloud node's firmware version lives in its config, which only an OTA
# changes; re-read it this often (one extra call per node per quarter hour).
CLOUD_FIRMWARE_CHECK_POLLS: Final = 15

# A node appears in the account before the cloud has stored its config;
# these bound the wait for the config.
CLOUD_CONFIG_WAIT_ATTEMPTS: Final = 6
CLOUD_CONFIG_WAIT_INTERVAL: Final = 5
