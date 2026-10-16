# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The forms the flows show, as voluptuous schemas."""

from typing import Any, Final

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import selector
import voluptuous as vol

from ..const import CONF_CONNECTION_MODE, CONF_CUSTOM_POP, CONF_RAINMAKER_REGION
from ..iot.specs.keys import (
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
    RAINMAKER_REGION_GLOBAL,
)
from .const import (
    CLOUD_ACTION_EXISTING,
    CLOUD_ACTION_PROVISION,
    FIELD_ACTION,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_POP,
    FIELD_OVERWRITE,
    FIELD_PROFILE_FILE,
    FIELD_REPOSITORY_SOURCE,
    FIELD_WIFI_PASSWORD,
    FIELD_WIFI_SSID,
    NAV_ACTION_BACK,
    NAV_ACTION_NEXT,
    PROFILE_FILE_ACCEPT,
    PROVISIONING_ACTION_BACK,
    PROVISIONING_ACTION_RETRY,
    SELECTOR_CLOUD_ACTION,
    SELECTOR_NAV_ACTION,
    SELECTOR_PROVISIONING_ACTION,
)

# Only the global region is offered: a China account signs in through WeChat
# or Apple ID, which needs a browser the CLI cannot open from inside Home
# Assistant. The client and ha_types know the region for when that changes.
RAINMAKER_LOGIN_SCHEMA: Final = vol.Schema(
    {
        vol.Required(
            CONF_RAINMAKER_REGION, default=RAINMAKER_REGION_GLOBAL
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[RAINMAKER_REGION_GLOBAL],
                translation_key=CONF_RAINMAKER_REGION,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_EMAIL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)

# The device is named rather than picked from a scan: the provisioning tool
# does its own BLE, outside Home Assistant's Bluetooth stack.
BLE_SCAN_SCHEMA: Final = vol.Schema(
    {
        vol.Required(FIELD_DEVICE_NAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }
)

WIFI_CONFIG_SCHEMA: Final = vol.Schema(
    {
        vol.Required(FIELD_WIFI_SSID): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        # Neither is a login for this site; see POP_INPUT_SCHEMA for why the
        # password manager is told so.
        vol.Optional(FIELD_WIFI_PASSWORD, default=""): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="new-password",
            )
        ),
        vol.Optional(FIELD_DEVICE_POP, default=""): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="new-password",
            )
        ),
    }
)

# Shown when provisioning did not finish, so the user can correct the WiFi
# details rather than start the flow again.
PROVISIONING_ERROR_SCHEMA: Final = vol.Schema(
    {
        vol.Required(
            FIELD_ACTION, default=PROVISIONING_ACTION_RETRY
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[PROVISIONING_ACTION_RETRY, PROVISIONING_ACTION_BACK],
                translation_key=SELECTOR_PROVISIONING_ACTION,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    }
)


def profile_import_schema(*, repository_source: bool = True) -> vol.Schema:
    """Return the first step's form: a profile of the user's own to import.

    Skippable: everything is optional. The pull request field is offered only
    when the flow says so (``REPOSITORY_SOURCE_FIELD_ENABLED``).
    """
    fields: dict[vol.Marker, Any] = {
        vol.Optional(FIELD_PROFILE_FILE): selector.FileSelector(
            selector.FileSelectorConfig(accept=PROFILE_FILE_ACCEPT)
        ),
        vol.Optional(FIELD_OVERWRITE, default=False): bool,
    }
    if repository_source:
        # The pull request to read the repository from; the step
        # fills in what is followed today as the suggested value.
        fields[vol.Optional(FIELD_REPOSITORY_SOURCE)] = selector.TextSelector()
    return vol.Schema(fields)


# Under every form that has a step before it: go on, or back. A dropdown of
# its own, set to going on, so that the usual path is pick and submit and the
# way back is there without two radio buttons under every form. The form's
# own field is optional so that going back needs nothing picked; going on
# with it empty is refused by the step.
NAV_ACTION_FIELD: Final[dict[vol.Marker, Any]] = {
    vol.Required(FIELD_ACTION, default=NAV_ACTION_NEXT): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[NAV_ACTION_NEXT, NAV_ACTION_BACK],
            translation_key=SELECTOR_NAV_ACTION,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    ),
}

POP_INPUT_SCHEMA: Final = vol.Schema(
    {
        # `new-password` keeps the browser's password manager from filling in
        # the Home Assistant login; browsers ignore `off` on password fields.
        vol.Optional(CONF_CUSTOM_POP, default=""): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="new-password",
            )
        ),
        **NAV_ACTION_FIELD,
    }
)

CONNECTION_MODE_SCHEMA: Final = vol.Schema(
    {
        vol.Required(
            CONF_CONNECTION_MODE, default=CONNECTION_MODE_LOCAL
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[CONNECTION_MODE_LOCAL, CONNECTION_MODE_CLOUD],
                # Labels come from translations rather than being written here,
                # so they follow the user's language.
                translation_key=CONF_CONNECTION_MODE,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    }
)

CLOUD_ACTION_SCHEMA: Final = vol.Schema(
    {
        vol.Required(
            FIELD_ACTION, default=CLOUD_ACTION_EXISTING
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[CLOUD_ACTION_EXISTING, CLOUD_ACTION_PROVISION],
                translation_key=SELECTOR_CLOUD_ACTION,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    }
)
