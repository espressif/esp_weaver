# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The config flow: import a profile, choose a product, then reach the device.

Step ownership is split across cooperative mixins (MRO left-to-right):

- ``LocalDiscoverySteps`` — mDNS list, security/PoP, local pending entry
- ``ProvisioningSteps`` — BLE Wi-Fi provision then cloud add (cloud path)
- ``BindingSteps`` / ``ProfileChoiceMixin`` (via local/cloud) — product picker
  and binding

``ESPWeaverConfigFlow`` only owns the first steps (profile import / vendor)
and the connection-mode gate. Cloud control is not supported in this release
(``CLOUD_CONTROL_ENABLED`` is False); the cloud steps are skipped, and the
mixins stay on the class so the tests that cover them keep running. The first
step's pull request field is likewise held back (``REPOSITORY_SOURCE_FIELD_ENABLED``
is False) while the mechanism behind it stays in place.
"""

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..const import (
    CLOUD_CONTROL_ENABLED,
    CONF_CONNECTION_MODE,
    REPOSITORY_SOURCE_FIELD_ENABLED,
)
from ..identity import DOMAIN
from ..iot.specs.keys import CONNECTION_MODE_CLOUD
from ..profiles import (
    MAX_PROFILE_SIZE,
    RepositoryRef,
    async_get_mirror,
    async_refresh_catalog,
    async_save_profile,
)
from .const import (
    ERROR_PROFILE_IMPORT_FAILED,
    ERROR_REPOSITORY_SOURCE_INVALID,
    ERROR_REPOSITORY_SOURCE_UNREACHABLE,
    FIELD_ACTION,
    FIELD_OVERWRITE,
    FIELD_PROFILE,
    FIELD_PROFILE_FILE,
    FIELD_REPOSITORY_SOURCE,
    FIELD_VENDOR,
    MESSAGE_PROFILE_IMPORTED,
    NAV_ACTION_BACK,
    PLACEHOLDER_ERROR,
    PLACEHOLDER_FILENAME,
    PLACEHOLDER_SOURCE,
    PROFILE_IMPORT_ERRORS,
    STEP_CONNECTION_MODE,
    STEP_PROFILE_SELECT,
    STEP_PROFILE_VENDOR,
    STEP_USER,
)
from .local import LocalDiscoverySteps
from .options import ESPWeaverOptionsFlow
from .provisioning import ProvisioningSteps
from .schemas import CONNECTION_MODE_SCHEMA, profile_import_schema

_LOGGER = logging.getLogger(__name__)


class ESPWeaverConfigFlow(LocalDiscoverySteps, ProvisioningSteps, domain=DOMAIN):
    """Handle a config flow for ESP-Weaver integration."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        # Name of the profile imported on the first step, reported on the next.
        self._imported: str = ""

    async def _async_imported(self) -> str:
        """Return the line confirming an import, empty if there was none."""
        if not self._imported:
            return ""
        line = await self._async_message(
            MESSAGE_PROFILE_IMPORTED, filename=self._imported
        )
        # The paragraph break belongs here, not in the string: translation
        # strings may not start or end with whitespace.
        return f"{line}\n\n"

    @staticmethod
    def _import_error(err: HomeAssistantError) -> tuple[str, dict[str, str]]:
        """Return the form error key for a refused import, and its placeholders."""
        key = err.translation_key or ""
        if err.translation_domain != DOMAIN or key not in PROFILE_IMPORT_ERRORS:
            return ERROR_PROFILE_IMPORT_FAILED, {PLACEHOLDER_ERROR: str(err)}
        return key, dict(err.translation_placeholders or {})

    async def _async_store_profile(self, file_id: str, overwrite: bool) -> Path:
        """Read one uploaded file and write it into the profile directory.

        Filed under the name it had on the user's computer.

        Raises:
            HomeAssistantError: The upload is gone, too large, unreadable, or
                not a usable profile; nothing is written. Each carries a
                translation key for the form.
        """

        def _consume() -> tuple[str, str]:
            """Take the upload, which also deletes it. Blocking."""
            with process_uploaded_file(self.hass, file_id) as uploaded:
                # Uploads may be far larger than any profile; check the size
                # before reading.
                if uploaded.stat().st_size > MAX_PROFILE_SIZE:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="upload_too_large",
                        translation_placeholders={
                            PLACEHOLDER_FILENAME: uploaded.name,
                            "limit": str(MAX_PROFILE_SIZE // 1024),
                        },
                    )
                return uploaded.name, uploaded.read_text(encoding="utf-8")

        try:
            name, content = await self.hass.async_add_executor_job(_consume)
        # Before ValueError, which it is a subclass of.
        except UnicodeDecodeError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="upload_not_text",
            ) from err
        except ServiceValidationError:
            # Raised by _consume itself, already carrying its own key.
            raise
        except ValueError as err:
            # The temporary file is dropped when Home Assistant restarts, so a
            # form left open across one lands here.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="upload_expired",
            ) from err
        except OSError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="upload_unreadable",
                translation_placeholders={PLACEHOLDER_ERROR: str(err)},
            ) from err

        return await async_save_profile(self.hass, name, content, overwrite)

    async def _async_follow(self, typed: str) -> tuple[str, dict[str, str]] | None:
        """Have the repository copy follow what was typed, or the index if nothing.

        A pull request is read at once, so that a wrong number or a typo is
        refused on this form rather than listed as an offline repository on
        the next; one that cannot be read leaves the copy following what it
        did. Clearing the field takes the copy back to the
        published index, which the vendor step then refreshes from.

        Returns:
            The form error key and its placeholders, or None when the copy
            follows what was typed.
        """
        try:
            ref = RepositoryRef.parse(typed) if typed else None
        except ValueError:
            return ERROR_REPOSITORY_SOURCE_INVALID, {}

        mirror = await async_get_mirror(self.hass)
        previous = mirror.source
        # The same source again has already been read once; the vendor step
        # refreshes from it without another request from here.
        if ref is not None and ref.spec == previous:
            return None
        await mirror.async_follow(ref)
        if ref is None:
            return None

        catalog = await async_refresh_catalog(self.hass, online=True)
        if catalog.index_error:
            await mirror.async_follow(
                RepositoryRef.parse(previous) if previous else None
            )
            return ERROR_REPOSITORY_SOURCE_UNREACHABLE, {
                PLACEHOLDER_SOURCE: ref.spec,
                PLACEHOLDER_ERROR: catalog.index_error,
            }
        # Read once for this flow; the vendor step lists it without asking again.
        self._catalog = catalog
        return None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow for this handler."""
        return ESPWeaverOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer to import a device profile, then go on to add the device.

        A profile has to be in place before the device it describes is added,
        and this is the only entry point before any device exists. Skippable,
        since the built-in profiles cover the standard device types; asked
        anyway so the mechanism is visible. When
        ``REPOSITORY_SOURCE_FIELD_ENABLED`` is on, the same step says where
        the repository is read from - the published index unless a pull
        request is named, for trying a profile before it merges - and shows
        what is followed today, so that it can be cleared here too. Off, the
        field is not shown and what the copy follows is left as it is.
        """
        errors: dict[str, str] = {}
        # Fills in whichever error is shown, so it is empty until there is one.
        # The step's own description interpolates nothing.
        placeholders: dict[str, str] = {}

        if user_input is not None:
            # A new submission either imports a new file or none; what an
            # earlier pass through this step imported is not reported again.
            self._imported = ""
            refused = None
            if REPOSITORY_SOURCE_FIELD_ENABLED:
                typed = (user_input.get(FIELD_REPOSITORY_SOURCE) or "").strip()
                refused = await self._async_follow(typed)
            if refused is not None:
                errors[FIELD_REPOSITORY_SOURCE], placeholders = refused
            elif not (file_id := user_input.get(FIELD_PROFILE_FILE)):
                return await self.async_step_profile_vendor()
            else:
                try:
                    path = await self._async_store_profile(
                        file_id, user_input.get(FIELD_OVERWRITE, False)
                    )
                except HomeAssistantError as err:
                    # Every reason to get here is one the user can act on.
                    errors["base"], placeholders = self._import_error(err)
                else:
                    # A new file is a product to offer, not a change to any
                    # binding; the vendor step reads the directory again and
                    # lists it.
                    self._imported = path.name
                    return await self.async_step_profile_vendor()

        followed = (
            (await async_get_mirror(self.hass)).source
            if REPOSITORY_SOURCE_FIELD_ENABLED
            else None
        )
        suggested = {FIELD_REPOSITORY_SOURCE: followed} if followed else None
        return self.async_show_form(
            step_id=STEP_USER,
            data_schema=self.add_suggested_values_to_schema(
                profile_import_schema(
                    repository_source=REPOSITORY_SOURCE_FIELD_ENABLED
                ),
                suggested,
            ),
            errors=errors,
            description_placeholders=placeholders,
            last_step=False,
        )

    async def async_step_connection_mode(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose how to reach the device: local over mDNS, or through RainMaker.

        With only local control on offer there is nothing to choose, and the
        flow goes straight to the network.
        """
        if not CLOUD_CONTROL_ENABLED:
            return await self._async_step_local_discovery()

        if user_input is not None:
            mode = user_input[CONF_CONNECTION_MODE]
            if mode == CONNECTION_MODE_CLOUD:
                return await self.async_step_rainmaker_login()
            # Default to local mode
            return await self._async_step_local_discovery()

        return self.async_show_form(
            step_id=STEP_CONNECTION_MODE,
            data_schema=CONNECTION_MODE_SCHEMA,
        )

    async def async_step_profile_vendor(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask whose product the device is; its products are the next step.

        Asked before the device is reached. The form offers to go back to the
        import step.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[FIELD_ACTION] == NAV_ACTION_BACK:
                return await self.async_step_user()
            error = await self._async_choose_vendor(user_input.get(FIELD_VENDOR))
            if error is None:
                return await self.async_step_profile_select()
            errors["base"] = error

        return await self._async_show_vendor_select(
            STEP_PROFILE_VENDOR,
            errors=errors,
            result=await self._async_imported(),
            back=True,
        )

    async def async_step_profile_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask which of the vendor's products the device is.

        The form offers to go back to the vendors.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[FIELD_ACTION] == NAV_ACTION_BACK:
                return await self.async_step_profile_vendor()
            error = await self._async_choose_profile(user_input.get(FIELD_PROFILE))
            if error is None:
                if self._pending_entry is None:
                    return await self.async_step_connection_mode()
                return await self._async_summary()
            errors["base"] = error

        return await self._async_show_profile_select(STEP_PROFILE_SELECT, errors=errors)
