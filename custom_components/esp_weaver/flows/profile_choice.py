# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Choosing the product a node is bound to, shared by both flows."""

from abc import abstractmethod
from collections import Counter
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryBaseFlow, ConfigFlowResult
from homeassistant.helpers import selector
import voluptuous as vol

from ..helpers.translations import async_common_message, async_common_messages
from ..profiles import (
    SOURCE_ORDER,
    SOURCE_REPO,
    CatalogError,
    ProductCandidate,
    ProfileCatalog,
    async_fetch_content,
    async_refresh_catalog,
    async_registry_for_candidate,
    async_sync_binding_issues,
    build_preview,
    misfit,
    reported_model,
    setup_report,
)
from .const import (
    ABORT_NODE_UNREAD,
    ABORT_SUMMARY_FAILED,
    ERROR_PROFILE_FETCH_FAILED,
    ERROR_PROFILE_UNKNOWN,
    ERROR_VENDOR_UNKNOWN,
    FIELD_PROFILE,
    FIELD_VENDOR,
    MESSAGE_MISMATCH_PREFIX,
    MESSAGE_REPOSITORY_FOLLOWING,
    MESSAGE_REPOSITORY_FOLLOWING_UNREAD,
    MESSAGE_REPOSITORY_OFFLINE,
    MESSAGE_SOURCE_PREFIX,
    MESSAGE_SOURCE_REPO_FOLLOWING,
    NOTHING,
    PLACEHOLDER_CURRENT,
    PLACEHOLDER_PRODUCT,
    PLACEHOLDER_PRODUCT_COUNT,
    PLACEHOLDER_REASON,
    PLACEHOLDER_REPORTED_MODEL,
    PLACEHOLDER_REPOSITORY,
    PLACEHOLDER_RESULT,
    PLACEHOLDER_VENDOR,
    PLACEHOLDER_VENDOR_COUNT,
    STEP_DEVICE_MISMATCH,
    STEP_PROFILE_VENDOR,
)
from .schemas import NAV_ACTION_FIELD

_LOGGER = logging.getLogger(__name__)


def _profile_labels(
    candidates: list[ProductCandidate],
    source_names: dict[str, str],
    language: str,
) -> dict[str, str]:
    """Return the line of each candidate in a vendor's product list, by key.

    A line is the product as the user reads it: "Lightbulb-A10". Where the
    list holds the same product in more than one version (identical copies
    are already one line; see ``ProfileCatalog.listed``), each of those lines
    says where its version is from in a word - "[built-in]", "[repository]",
    "[your folder]", "[cached]" - and nothing more: the user picks, nothing is
    chosen for them, and a path or hash would only be noise. A product with
    one version is not marked at all, however many places the vendor's other
    products come from. Only when two lines would still read the same - two
    files in the folder describing one product differently, two kept copies -
    is the file name, or the copy's hash, added to tell them apart.
    """
    places = Counter(candidate.id for candidate in candidates)
    labels = {
        candidate.key: (
            f"{candidate.localized_name(language)}  "
            f"[{source_names.get(candidate.source, candidate.source)}]"
            if places[candidate.id] > 1
            else candidate.localized_name(language)
        )
        for candidate in candidates
    }
    repeated = {label for label, n in Counter(labels.values()).items() if n > 1}
    for candidate in candidates:
        if (label := labels[candidate.key]) not in repeated:
            continue
        detail = candidate.location or candidate.hash
        labels[candidate.key] = (
            f"{label[:-1]}: {detail}]"
            if label.endswith("]")
            else f"{label}  [{detail}]"
        )
    return labels


class ProfileChoiceMixin(ConfigEntryBaseFlow):
    """Choosing a product for a node, shared by the config and options flows.

    Only what happens on confirmation differs.
    """

    def _init_profile_choice(self) -> None:
        """Start with nothing chosen and nothing known about the node."""
        self._catalog: ProfileCatalog | None = None
        # The node's object model and current values, from whichever path
        # reached the node. Empty when it could not be read.
        self._node_config: dict[str, Any] = {}
        self._node_params: dict[str, Any] = {}
        self._node_id: str = ""
        # Whether the node's model is held against the product; the local path
        # clears it for a device whose mDNS record carried no model.
        self._identity_advertised: bool = True
        self._chosen: ProductCandidate | None = None
        self._report: dict[str, Any] | None = None
        # The vendor picked in the first step; the second lists its products.
        self._vendor: str | None = None

    async def _async_message(self, key: str, **placeholders: str) -> str:
        """Return one server-rendered fragment from the common catalogue."""
        return await async_common_message(self.hass, key, **placeholders)

    async def _async_source_names(self) -> dict[str, str]:
        """Return how each candidate source is named in the list.

        The repository is named by the pull request while one is followed,
        since that is what its author is looking for in the list.
        """
        assert self._catalog is not None
        keys = {f"{MESSAGE_SOURCE_PREFIX}{source}": source for source in SOURCE_ORDER}
        named = await async_common_messages(self.hass, *keys)
        names = {source: named[key] for key, source in keys.items()}
        if self._catalog.repository_source:
            names[SOURCE_REPO] = await self._async_message(
                MESSAGE_SOURCE_REPO_FOLLOWING, source=self._catalog.repository_source
            )
        return names

    def _product_title(self, candidate: ProductCandidate) -> str:
        """Return a product title in Home Assistant's configured language."""
        return candidate.localized_title(self.hass.config.language)

    async def _async_repository_notice(self) -> str:
        """Return what to say about the repository copy, or nothing.

        A line when the copy follows a pull request rather than the
        published index, and a line when it could not be refreshed.
        """
        assert self._catalog is not None
        catalog = self._catalog
        lines: list[str] = []
        if catalog.repository_source:
            if catalog.repository_commit:
                lines.append(
                    await self._async_message(
                        MESSAGE_REPOSITORY_FOLLOWING,
                        source=catalog.repository_source,
                        commit=catalog.repository_commit[:7],
                    )
                )
            else:
                lines.append(
                    await self._async_message(
                        MESSAGE_REPOSITORY_FOLLOWING_UNREAD,
                        source=catalog.repository_source,
                    )
                )
        if catalog.index_error:
            lines.append(await self._async_message(MESSAGE_REPOSITORY_OFFLINE))
        # The paragraph break belongs here, not in the string: translation
        # strings may not start or end with whitespace.
        return "".join(f"\n\n{line}" for line in lines)

    async def _async_show_vendor_select(
        self,
        step_id: str,
        *,
        errors: dict[str, str] | None = None,
        current: str | None = None,
        result: str = "",
        back: bool = False,
    ) -> ConfigFlowResult:
        """Show the vendors that have products, nothing preselected.

        The catalog is read afresh here, since the user may have just dropped a
        file in. The repository index is refreshed once per flow, the first
        time this form shows; coming back later rereads the disk only. A
        repository product's file is downloaded when it is picked.
        The bindings are checked against what was read, so a newer profile
        found on the way in raises its repair even if the flow is abandoned.

        Args:
            step_id: Which form to show; the options flow has its own.
            errors: What went wrong with the last choice, if anything.
            current: The product the device is bound to today.
            result: Confirmation of a file imported the step before.
            back: Whether there is a step before, to offer going back to.
        """
        self._catalog = await async_refresh_catalog(
            self.hass, online=self._catalog is None
        )
        async_sync_binding_issues(self.hass, self._catalog)
        self._vendor = None
        vendors = self._catalog.vendors()
        vendor_names: dict[str, str] = {}
        for candidate in self._catalog.listed():
            name = candidate.localized_vendor(self.hass.config.language)
            # A profile without display metadata must not overwrite the
            # localized name supplied by another product of the same vendor.
            if candidate.vendor not in vendor_names or name != candidate.vendor:
                vendor_names[candidate.vendor] = name

        # Just the names: a list of brands, as on a shelf.
        options = [
            selector.SelectOptionDict(
                label=vendor_names.get(vendor, vendor), value=vendor
            )
            for vendor in vendors
        ]
        repository = await self._async_repository_notice()
        # This step has no fixed sentence before the notice. An imported-file
        # confirmation already ends with its own paragraph break.
        if step_id == STEP_PROFILE_VENDOR:
            repository = repository.lstrip("\n")
        vendor_field = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options,
                mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {vol.Optional(FIELD_VENDOR): vendor_field, **NAV_ACTION_FIELD}
                if back
                else {vol.Required(FIELD_VENDOR): vendor_field}
            ),
            errors=errors or {},
            description_placeholders={
                PLACEHOLDER_VENDOR_COUNT: str(len(vendors)),
                # Keep placeholders used by earlier translations so a
                # frontend translation cache cannot break during an upgrade.
                PLACEHOLDER_PRODUCT_COUNT: str(len(self._catalog.listed())),
                PLACEHOLDER_REPOSITORY: repository,
                PLACEHOLDER_CURRENT: current or NOTHING,
                PLACEHOLDER_RESULT: result,
            },
        )

    async def _async_choose_vendor(self, vendor: str | None) -> str | None:
        """Take the vendor the user picked; return the form error, if any."""
        # Read when the vendor form was shown, which is the only way here.
        assert self._catalog is not None
        if not vendor or vendor not in self._catalog.vendors():
            return ERROR_VENDOR_UNKNOWN
        self._vendor = vendor
        return None

    async def _async_show_profile_select(
        self,
        step_id: str,
        *,
        errors: dict[str, str] | None = None,
        current: str | None = None,
    ) -> ConfigFlowResult:
        """Show the chosen vendor's products, grouped by kind, nothing preselected.

        Args:
            step_id: Which form to show; the options flow has its own.
            errors: What went wrong with the last choice, if anything.
            current: The product the device is bound to today.
        """
        assert self._catalog is not None
        candidates = self._catalog.listed(self._vendor)
        vendor_name = (
            candidates[0].localized_vendor(self.hass.config.language)
            if candidates
            else self._vendor or NOTHING
        )
        labels = _profile_labels(
            candidates,
            await self._async_source_names(),
            self.hass.config.language,
        )
        options = [
            selector.SelectOptionDict(label=labels[candidate.key], value=candidate.key)
            for candidate in candidates
        ]
        repository = await self._async_repository_notice()

        # The vendor list is always the step before, in both flows.
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Optional(FIELD_PROFILE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            # custom_value makes the frontend render a
                            # filtering combo box; free text comes back as
                            # an unknown key and gets profile_unknown.
                            custom_value=True,
                        )
                    ),
                    **NAV_ACTION_FIELD,
                }
            ),
            errors=errors or {},
            description_placeholders={
                PLACEHOLDER_VENDOR: vendor_name,
                PLACEHOLDER_PRODUCT_COUNT: str(len(candidates)),
                # Kept for compatibility with translations cached before the
                # repository notice was removed from this page.
                PLACEHOLDER_REPOSITORY: repository,
                PLACEHOLDER_CURRENT: current or NOTHING,
            },
        )

    async def _async_choose_profile(self, candidate_key: str | None) -> str | None:
        """Take the user's choice and fetch its content; return any form error."""
        assert self._catalog is not None
        candidate = self._catalog.get(candidate_key or "")
        if candidate is None:
            return ERROR_PROFILE_UNKNOWN

        try:
            fetched = await async_fetch_content(self.hass, candidate)
        except CatalogError as err:
            _LOGGER.warning("Profile %s could not be fetched: %s", candidate.id, err)
            return ERROR_PROFILE_FETCH_FAILED

        self._chosen = fetched
        self._report = None
        return None

    async def _async_rehearse(self) -> None:
        """Work out what the chosen product would build for the node, if not done."""
        if self._report is None and self._chosen is not None:
            self._report = await self._async_report(self._chosen)

    async def _async_report(self, candidate: ProductCandidate) -> dict[str, Any] | None:
        """Return what this product would build for the node, or None on failure."""
        try:
            registry = await async_registry_for_candidate(self.hass, candidate)
            preview = build_preview(
                registry, self._node_id, self._node_config, self._node_params
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            # Worth a traceback, but not worth failing the setup over: a
            # rehearsal that raises is refused rather than bound blindly.
            _LOGGER.exception(
                "Could not summarise %s for %s", candidate.id, self._node_id
            )
            return None

        return setup_report(preview)

    def _misfit(self) -> str | None:
        """Return why the node read is not the chosen product's, or None.

        The check that counts: the mDNS record was only a headline, and a
        cloud device gave none. A device that advertised no model is not
        judged: its firmware reports its project name, which names no product.
        """
        assert self._chosen is not None
        if not self._identity_advertised:
            return None
        return misfit(self._chosen, reported_model(self._node_config))

    async def _async_show_device_mismatch(self, reason: str) -> ConfigFlowResult:
        """Say that the device is not the chosen product's, and offer another."""
        chosen = self._chosen
        return self.async_show_form(
            step_id=STEP_DEVICE_MISMATCH,
            data_schema=vol.Schema({}),
            description_placeholders={
                PLACEHOLDER_PRODUCT: (
                    self._product_title(chosen) if chosen else NOTHING
                ),
                PLACEHOLDER_REASON: await self._async_message(
                    f"{MESSAGE_MISMATCH_PREFIX}{reason}"
                ),
                PLACEHOLDER_REPORTED_MODEL: reported_model(self._node_config)
                or NOTHING,
            },
        )

    def _log_setup_report(self) -> None:
        """Write what the product builds, for someone reading the log.

        The form that used to spell this out is gone: end users do not act on
        entity counts. A skipped entity and an undeclared type are already
        logged by the resolver; this line is the one-place count.
        """
        report = self._report or {}
        created = report.get("created") or []
        skipped = report.get("skipped") or []
        unknown = report.get("unknown_types") or []
        product = self._chosen.id if self._chosen else "?"
        names = [str(entry.get("name") or entry.get("key")) for entry in created]
        _LOGGER.debug(
            "Product %s on %s creates %d entities: %s",
            product,
            self._node_id,
            len(created),
            ", ".join(names) if names else "none",
        )
        if skipped:
            left_out = [str(entry.get("name") or entry.get("key")) for entry in skipped]
            _LOGGER.info(
                "Product %s on %s left out %d entities: %s",
                product,
                self._node_id,
                len(skipped),
                ", ".join(left_out),
            )
        if unknown:
            types = [str(entry.get("type")) for entry in unknown]
            _LOGGER.info(
                "Product %s on %s has %d types with no profile: %s",
                product,
                self._node_id,
                len(unknown),
                ", ".join(types),
            )

    async def _async_summary(self) -> ConfigFlowResult:
        """Rehearse the chosen product against the node, then bind it.

        A node that never said what it is made of, or a rehearsal that failed,
        ends the flow rather than binding a choice nobody has seen the result of.
        What the product would build is written to the log, not shown as a form.
        """
        assert self._chosen is not None
        if not self._node_config:
            return self.async_abort(reason=ABORT_NODE_UNREAD)
        if (reason := self._misfit()) is not None:
            return await self._async_show_device_mismatch(reason)

        await self._async_rehearse()
        if self._report is None:
            return self.async_abort(reason=ABORT_SUMMARY_FAILED)
        self._log_setup_report()
        return await self._async_confirm()

    async def async_step_device_mismatch(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """The device is not the chosen product's; the way on is another product."""
        if user_input is not None:
            return await self._async_back_to_vendors()
        return await self._async_summary()

    @abstractmethod
    async def _async_confirm(self) -> ConfigFlowResult:
        """Act on a confirmed choice: create the entry, or rebind the existing one.

        The one thing the two flows do not share.
        """

    @abstractmethod
    async def _async_back_to_vendors(self) -> ConfigFlowResult:
        """Show the vendor step again, through the flow's own step method."""
