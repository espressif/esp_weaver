# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Parsing of the properties an ESP device answers local control queries with."""

import json
import logging
from typing import Any

from ..specs.device_specs import CONFIG_PROPERTY_NAMES
from ..specs.keys import (
    KEY_FW_VERSION,
    KEY_INFO,
    KEY_MODEL,
    KEY_NAME,
    KEY_PARAMS,
    KEY_VALUE,
)

_LOGGER = logging.getLogger(__name__)

# RainMaker's generic model: reference firmware reports it, and it names no
# particular hardware.
MODEL_GENERIC_REPORTED = "*"


def decode_property_value(prop_value: object) -> dict | None:
    """Return one property's value as a mapping (from bytes, str or dict), or None.

    Anything else, None included, is not a value and gives None.
    """
    if isinstance(prop_value, dict):
        return prop_value

    if isinstance(prop_value, bytes):
        # ESP devices send UTF-8 encoded JSON
        try:
            text = prop_value.decode("utf-8")
        except UnicodeDecodeError:
            # Latin-1 can decode any byte sequence
            text = prop_value.decode("latin-1")
    elif isinstance(prop_value, str):
        text = prop_value
    else:
        return None

    text = text.strip()
    if not text.startswith("{"):
        return None

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        _LOGGER.debug("Failed to parse JSON property value")
        return None
    if not isinstance(result, dict):
        _LOGGER.debug("JSON parsed to non-dict type: %s", type(result).__name__)
        return None
    return result


def get_raw_params(properties: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Return the params property as sent, ``{device_name: {param: value}}``.

    An unnamed property counts: firmware may report params without naming it.
    """
    if not properties:
        return None

    for prop in properties:
        prop_name = prop.get(KEY_NAME, "")
        if prop_name and prop_name != KEY_PARAMS:
            continue
        decoded = decode_property_value(prop.get(KEY_VALUE))
        if decoded:
            return decoded
    return None


def find_config_property(
    properties: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Return the property carrying the node's object model, undecoded.

    Firmware has called it several things; the names are tried in order.
    """
    if not properties:
        return None

    for config_name in CONFIG_PROPERTY_NAMES:
        for prop in properties:
            if prop.get(KEY_NAME) == config_name and isinstance(
                prop.get(KEY_VALUE), (bytes, str, dict)
            ):
                return prop
    return None


def get_raw_config(properties: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Return the node's object model as a plain dict."""
    config_property = find_config_property(properties)
    if config_property is None:
        return None
    return decode_property_value(config_property.get(KEY_VALUE))


def firmware_version_from_config(config: dict[str, Any] | None) -> str | None:
    """Return the firmware version a node's config names, if it names one.

    Read on its own because it is the one field an OTA changes without the
    object model changing.
    """
    if not config:
        return None

    info = config.get(KEY_INFO)
    if not isinstance(info, dict):
        return None

    version = info.get(KEY_FW_VERSION)
    return str(version) if version else None


def model_from_config(config: dict[str, Any] | None) -> str | None:
    """Return ``info.model`` from a node's config, if present. No fallback.."""
    if not config:
        return None

    info = config.get(KEY_INFO)
    if not isinstance(info, dict):
        return None

    model = info.get(KEY_MODEL)
    return str(model) if model else None


def model_id_from_config(
    config: dict[str, Any] | None, *, identity_advertised: bool
) -> str | None:
    """Return the advertised model, except the generic ``"*"``.

    Whether firmware set a model comes from its mDNS record, not from comparing
    the model with the project name: firmware may deliberately use its project
    name as its model.
    """
    if not config or not identity_advertised:
        return None

    info = config.get(KEY_INFO)
    if not isinstance(info, dict):
        return None

    model = info.get(KEY_MODEL)
    if not model or model == MODEL_GENERIC_REPORTED:
        return None
    return str(model)


def firmware_version_from_properties(
    properties: list[dict[str, Any]] | None,
) -> str | None:
    """Return the firmware version a local read's config carries, if any."""
    return firmware_version_from_config(get_raw_config(properties))
