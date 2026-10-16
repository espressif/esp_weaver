# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Device profiles: products, the catalog they are offered from, and bindings.

A profile is the only place the mapping from RainMaker devices to Home
Assistant entities is expressed; no platform hardcodes a device type or
parameter name. Each device is bound to exactly one product, chosen when it
was added. Only what the rest of the integration uses is exported here.
"""

from .binding import EntityBinding, ParamBinding, ThresholdRole
from .catalog import (
    ProfileCatalog,
    async_fetch_content,
    async_get_catalog,
    async_refresh_catalog,
)
from .download import MAX_PROFILE_SIZE
from .fit import MISFIT_MODEL, misfit, reported_model
from .github import RepositoryRef
from .importer import async_import_profile, async_save_profile
from .loader import CUSTOM_PROFILE_DIR, PROBLEM_MISPLACED, PROFILE_DIR
from .mirror import CatalogError, async_get_mirror
from .preview import REASON_CONFLICT, REASON_MISSING_PARAMS, build_preview, setup_report
from .product import (
    CATEGORY_ORDER,
    SOURCE_CACHED,
    SOURCE_LOCAL,
    SOURCE_ORDER,
    SOURCE_REPO,
    ProductCandidate,
    category_of,
)
from .resolver import resolve_bindings
from .schema import MODEL_GENERIC, THRESHOLD_END_MIN
from .selection import (
    ISSUE_DATA_HASH,
    ISSUE_DATA_PRODUCT,
    ISSUE_DATA_SOURCE,
    OPTION_PROFILE,
    DeviceIdentity,
    ProfileBinding,
    ProfileMissingError,
    async_bind,
    async_keep,
    async_prune_store,
    async_registry_for_candidate,
    async_registry_for_entry,
    async_stored_product_for_entry,
    async_sync_binding_issues,
    entries_to_update,
    options_with_binding,
)

__all__ = [
    "CATEGORY_ORDER",
    "CUSTOM_PROFILE_DIR",
    "ISSUE_DATA_HASH",
    "ISSUE_DATA_PRODUCT",
    "ISSUE_DATA_SOURCE",
    "MAX_PROFILE_SIZE",
    "MISFIT_MODEL",
    "MODEL_GENERIC",
    "OPTION_PROFILE",
    "PROBLEM_MISPLACED",
    "PROFILE_DIR",
    "REASON_CONFLICT",
    "REASON_MISSING_PARAMS",
    "SOURCE_CACHED",
    "SOURCE_LOCAL",
    "SOURCE_ORDER",
    "SOURCE_REPO",
    "THRESHOLD_END_MIN",
    "CatalogError",
    "DeviceIdentity",
    "EntityBinding",
    "ParamBinding",
    "ProductCandidate",
    "ProfileBinding",
    "ProfileCatalog",
    "ProfileMissingError",
    "RepositoryRef",
    "ThresholdRole",
    "async_bind",
    "async_fetch_content",
    "async_get_catalog",
    "async_get_mirror",
    "async_import_profile",
    "async_keep",
    "async_prune_store",
    "async_refresh_catalog",
    "async_registry_for_candidate",
    "async_registry_for_entry",
    "async_save_profile",
    "async_stored_product_for_entry",
    "async_sync_binding_issues",
    "build_preview",
    "category_of",
    "entries_to_update",
    "misfit",
    "options_with_binding",
    "reported_model",
    "resolve_bindings",
    "setup_report",
]
