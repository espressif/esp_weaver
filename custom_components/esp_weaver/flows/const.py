# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Names shared by the config and options flows; all resolve against strings.json."""

from typing import Final

# The step ids. Each one names a form in strings.json, so the two have to agree.
STEP_USER: Final = "user"
STEP_DEVICE_SETUP: Final = "device_setup"
STEP_POP_INPUT: Final = "pop_input"
STEP_CONNECTION_MODE: Final = "connection_mode"
STEP_RAINMAKER_LOGIN: Final = "rainmaker_login"
STEP_CLOUD_ACTION_SELECT: Final = "cloud_action_select"
STEP_CLOUD_DEVICE_SELECT: Final = "cloud_device_select"
STEP_BLE_SCAN: Final = "ble_scan"
STEP_WIFI_CONFIG: Final = "wifi_config"
# Provisioning runs as a task behind a progress dialog (STEP_PROVISIONING,
# whose text is the `progress` entry), lands on a result step that is never
# shown, and on failure on a form offering retry or back.
STEP_PROVISIONING: Final = "provisioning"
STEP_PROVISIONING_RESULT: Final = "provisioning_result"
STEP_PROVISIONING_FAILED: Final = "provisioning_failed"
PROGRESS_PROVISIONING: Final = "provisioning"
# Which product the device is: the vendor, then one of its products, asked
# before the device is reached. The options flow shows the same two steps
# (its first step is `init`).
STEP_PROFILE_VENDOR: Final = "profile_vendor"
STEP_PROFILE_SELECT: Final = "profile_select"
STEP_INIT: Final = "init"
# Shown when the device, once read, reports another model. The only way on
# is another product.
STEP_DEVICE_MISMATCH: Final = "device_mismatch"
# The choice under a form that has a step before it: go on with what was
# picked, or back. Home Assistant's dialogs have no back button, so the forms
# carry their own.
SELECTOR_NAV_ACTION: Final = "nav_action"
NAV_ACTION_NEXT: Final = "next"
NAV_ACTION_BACK: Final = "back"

# The errors a step can put on a field, and the reasons a flow can abort. Both
# resolve against strings.json.
ERROR_POP_REQUIRED: Final = "pop_required"
ERROR_INVALID_POP: Final = "invalid_pop"
ERROR_INVALID_DEVICE: Final = "invalid_device_selection"
ERROR_CANNOT_CONNECT: Final = "cannot_connect"
ERROR_RAINMAKER_NOT_LOGGED_IN: Final = "rainmaker_not_logged_in"
ERROR_RAINMAKER_LOGIN_FAILED: Final = "rainmaker_login_failed"
ERROR_PROVISIONING_FAILED: Final = "provisioning_failed"
ERROR_PROVISIONING_NOT_IN_CLOUD: Final = "provisioning_not_in_cloud"
# Several nodes joined the account during provisioning and the tool did not
# say which is ours; the detail lists them.
ERROR_PROVISIONING_AMBIGUOUS: Final = "provisioning_ambiguous"
ERROR_WIFI_SSID_REQUIRED: Final = "wifi_ssid_required"
ERROR_WIFI_PASSWORD_REQUIRED: Final = "wifi_password_required"
ERROR_PROFILE_IMPORT_FAILED: Final = "profile_import_failed"
# What was typed as the repository to follow is not a pull request number; or
# it is, but its index could not be read from GitHub.
ERROR_REPOSITORY_SOURCE_INVALID: Final = "repository_source_invalid"
ERROR_REPOSITORY_SOURCE_UNREACHABLE: Final = "repository_source_unreachable"
# The chosen product is not in the list any more, or could not be fetched.
ERROR_PROFILE_UNKNOWN: Final = "profile_unknown"
ERROR_VENDOR_UNKNOWN: Final = "vendor_unknown"
ERROR_PROFILE_FETCH_FAILED: Final = "profile_fetch_failed"
ABORT_NO_DEVICES: Final = "no_devices_found"
# The picked device never said what security it runs, so there is no telling
# whether to ask for a PoP; the flow ends rather than guess.
ABORT_SECURITY_UNKNOWN: Final = "security_unknown"
# The device was reached but never said what it is made of, so no product can
# be rehearsed against it and it is not added.
ABORT_NODE_UNREAD: Final = "node_unread"
# The chosen product could not be rehearsed against the node (logged with a
# traceback). Nothing is bound on a choice nobody has seen the result of.
ABORT_SUMMARY_FAILED: Final = "summary_failed"
# Devices were found, but none of them is the chosen product's - or none said
# what it is, which older firmware does not.
ABORT_NO_FITTING_DEVICES: Final = "no_fitting_devices"

# Why an import was refused. Each is both the exception's translation key and
# a form error; anything raised with a key not listed here falls back to the
# generic line rather than showing a bare key.
PROFILE_IMPORT_ERRORS: Final[frozenset[str]] = frozenset(
    {
        "import_bad_filename",
        "import_exists",
        "import_write_failed",
        "upload_expired",
        "upload_invalid",
        "upload_not_text",
        "upload_too_large",
        "upload_unreadable",
    }
)
ABORT_NO_NEW_DEVICES: Final = "no_new_devices_found"

# Form fields, and the placeholders a step's text is filled in with. Each field
# names a key in strings.json, so the two have to agree.
FIELD_ACTION: Final = "action"
FIELD_SELECTED_DEVICE: Final = "selected_device"
FIELD_VENDOR: Final = "vendor"
FIELD_PROFILE: Final = "profile"
# The first step: a profile of the user's own to import, and the pull
# request to read the repository from.
FIELD_PROFILE_FILE: Final = "profile_file"
FIELD_OVERWRITE: Final = "overwrite"
FIELD_REPOSITORY_SOURCE: Final = "repository_source"
FIELD_DEVICE_NAME: Final = "device_name"
FIELD_DEVICE_POP: Final = "device_pop"
FIELD_WIFI_SSID: Final = "wifi_ssid"
FIELD_WIFI_PASSWORD: Final = "wifi_password"
PLACEHOLDER_DEVICE_COUNT: Final = "device_count"
PLACEHOLDER_DEVICE_NAME: Final = "device_name"
PLACEHOLDER_FILENAME: Final = "filename"
PLACEHOLDER_ERROR: Final = "error"
PLACEHOLDER_RESULT: Final = "result"
PLACEHOLDER_COUNT: Final = "count"
PLACEHOLDER_ROWS: Final = "rows"
PLACEHOLDER_PRODUCT: Final = "product"
PLACEHOLDER_PRODUCT_COUNT: Final = "product_count"
PLACEHOLDER_REPOSITORY: Final = "repository"
PLACEHOLDER_SOURCE: Final = "source"
PLACEHOLDER_CURRENT: Final = "current"
PLACEHOLDER_VENDOR: Final = "vendor"
PLACEHOLDER_VENDOR_COUNT: Final = "vendor_count"
PLACEHOLDER_FOUND: Final = "found"
PLACEHOLDER_MATCHED: Final = "matched"
PLACEHOLDER_SILENT_NOTE: Final = "silent_note"
PLACEHOLDER_REPORTED_MODEL: Final = "reported_model"
PLACEHOLDER_REASON: Final = "reason"

# Marks a cloud device's entry title, so it is told from a local twin.
CLOUD_TITLE_SUFFIX: Final = " (Cloud)"

# What a device is called when nothing says what it is: the device list's
# line for firmware that advertises no model, and the entry title when the
# node names nothing. Never a vendor's name - the products are many vendors'
# - and never the node id, which means nothing to the user. English, like the
# product labels it sits among, which come from the profiles as written.
OTHER_DEVICE_LABEL: Final = "Other device"

# Stands in for a table cell or a list with nothing in it. A dash rather than a
# word, so that it reads the same in every language the form is shown in.
NOTHING: Final = "—"

# Selector fields whose option labels come from the `selector` translations.
SELECTOR_CLOUD_ACTION: Final = "cloud_action"
SELECTOR_PROVISIONING_ACTION: Final = "provisioning_action"
CLOUD_ACTION_EXISTING: Final = "existing"
CLOUD_ACTION_PROVISION: Final = "provision"
PROVISIONING_ACTION_RETRY: Final = "retry"
PROVISIONING_ACTION_BACK: Final = "back"

# What an unprovisioned RainMaker device calls itself over BLE.
PROVISIONING_NAME_PREFIX: Final = "PROV_"

# What the upload dialog offers: both extensions plus the media types
# browsers attach to them.
PROFILE_FILE_ACCEPT: Final = ".yaml,.yml,text/yaml,application/x-yaml,text/plain"

# Fragments rendered on the server (see helpers.translations): labels of
# dynamic list entries and pieces spliced into a step description.
MESSAGE_PROFILE_IMPORTED: Final = "profile_imported"
MESSAGE_DEVICE_ONLINE: Final = "device_online"
MESSAGE_DEVICE_OFFLINE: Final = "device_offline"
MESSAGE_DEVICES_MATCHED: Final = "devices_matched"
MESSAGE_OTHER_DEVICES: Final = "other_devices"
MESSAGE_REPOSITORY_OFFLINE: Final = "repository_offline"
# The copy follows a pull request: read at a commit, or not yet.
MESSAGE_REPOSITORY_FOLLOWING: Final = "repository_following"
MESSAGE_REPOSITORY_FOLLOWING_UNREAD: Final = "repository_following_unread"

# How long to wait for a provisioned node to turn up in the account.
# Self-claiming alone takes 30-60s on a device never claimed before.
PROVISION_POLL_INTERVAL: Final = 5
PROVISION_POLL_ATTEMPTS: Final = 24

# The device list comes back empty, not failed, when the API is having a
# moment, so an empty list is given another chance.
CLOUD_DEVICE_FETCH_ATTEMPTS: Final = 3
CLOUD_DEVICE_FETCH_INTERVAL: Final = 2

# Security detection connects to each discovered device: a per-device timeout
# and an overall backstop.
SECURITY_DETECT_TIMEOUT: Final = 5.0
SECURITY_DETECT_TOTAL_TIMEOUT: Final = 10.0

# Source and group labels for the product list, from the common catalogue.
MESSAGE_SOURCE_PREFIX: Final = "source_"
# How the repository is named in the list while a pull request is followed.
MESSAGE_SOURCE_REPO_FOLLOWING: Final = "source_repo_following"
# Why a device read in full is not the chosen product's: one sentence per
# reason profiles.fit can give, from the common catalogue.
MESSAGE_MISMATCH_PREFIX: Final = "mismatch_"
