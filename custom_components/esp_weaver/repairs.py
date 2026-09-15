# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The fixable repair: adopting a product's newer content.

Shows what would change for each device on the product, and on confirmation
rebinds every one of them at once.
"""

from typing import TYPE_CHECKING, Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .const import CONF_NODE_ID
from .data import domain_data
from .flows.const import PLACEHOLDER_COUNT, PLACEHOLDER_PRODUCT, PLACEHOLDER_ROWS
from .profiles import (
    ISSUE_DATA_HASH,
    ISSUE_DATA_PRODUCT,
    ISSUE_DATA_SOURCE,
    CatalogError,
    ProductCandidate,
    ProfileMissingError,
    async_bind,
    async_fetch_content,
    async_get_catalog,
    async_registry_for_candidate,
    async_registry_for_entry,
    build_preview,
    entries_to_update,
    setup_report,
)

if TYPE_CHECKING:
    from .iot.client.device_api import ESPWeaverApi

STEP_CONFIRM = "confirm"
ABORT_GONE = "profile_update_gone"
ABORT_FETCH_FAILED = "profile_update_fetch_failed"

# Stands in for a count that could not be made.
UNKNOWN = "?"


class ProfileUpdateFlow(RepairsFlow):
    """Offer every device on a product the product's current content."""

    def __init__(self, product_id: str, source: str, content_hash: str) -> None:
        """Remember which product's copy, and which content, the issue was raised for."""
        self._product_id = product_id
        self._source = source
        self._hash = content_hash

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show what changes, or apply it."""
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show the before and after for each device, and rebind on confirmation."""
        catalog = await async_get_catalog(self.hass)
        candidate = catalog.by_hash(self._hash, self._product_id, self._source)
        # The catalog moved on since the issue was raised; the next sync
        # re-raises it if still due.
        if candidate is None:
            return self.async_abort(reason=ABORT_GONE)

        try:
            fetched = await async_fetch_content(self.hass, candidate)
        except CatalogError:
            return self.async_abort(reason=ABORT_FETCH_FAILED)

        # Each rebind sets its entry up again; the last prunes what nothing
        # points at any more.
        entries = entries_to_update(
            self.hass, self._product_id, self._source, fetched.hash
        )
        if user_input is not None:
            for entry in entries:
                await async_bind(self.hass, entry, fetched)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id=STEP_CONFIRM,
            data_schema=vol.Schema({}),
            description_placeholders={
                PLACEHOLDER_PRODUCT: fetched.title,
                PLACEHOLDER_COUNT: str(len(entries)),
                PLACEHOLDER_ROWS: await _rows(self.hass, entries, fetched),
            },
        )


async def _rows(
    hass: HomeAssistant, entries: list[ConfigEntry], candidate: ProductCandidate
) -> str:
    """Return one table row per device: entities today, entities after."""
    api = domain_data(hass).api
    rows = []
    for entry in entries:
        before, after = await _counts(hass, api, entry, candidate)
        rows.append(f"| {entry.title} | {before} | {after} |")
    return "\n".join(rows) or "| — | — | — |"


async def _counts(
    hass: HomeAssistant,
    api: "ESPWeaverApi | None",
    entry: ConfigEntry,
    candidate: ProductCandidate,
) -> tuple[str, str]:
    """Return how many entities the device gets now and would get after."""
    node_id = entry.data[CONF_NODE_ID]
    if api is None:
        return UNKNOWN, UNKNOWN
    node_config, params = api.get_reported_config(node_id)
    if not node_config:
        return UNKNOWN, UNKNOWN

    try:
        current = await async_registry_for_entry(hass, entry)
        proposed = await async_registry_for_candidate(hass, candidate)
        before = setup_report(build_preview(current, node_id, node_config, params))
        after = setup_report(build_preview(proposed, node_id, node_config, params))
    except (ProfileMissingError, KeyError, TypeError, ValueError):
        return UNKNOWN, UNKNOWN
    return str(len(before["created"])), str(len(after["created"]))


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the flow that fixes one of this integration's fixable issues."""
    data = data or {}
    return ProfileUpdateFlow(
        str(data.get(ISSUE_DATA_PRODUCT, "")),
        str(data.get(ISSUE_DATA_SOURCE, "")),
        str(data.get(ISSUE_DATA_HASH, "")),
    )
