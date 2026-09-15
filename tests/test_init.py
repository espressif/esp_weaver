# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver integration setup and unload."""

from collections.abc import Generator
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, SERVICE_RELOAD
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
import voluptuous as vol
import yaml

from custom_components import esp_weaver
from custom_components.esp_weaver import (
    ATTR_FILE,
    ATTR_FILENAME,
    ATTR_OVERWRITE,
    ATTR_PROFILES,
    ATTR_URL,
    _cleanup_integration,
    _get_or_create_api,
    _get_or_create_cli_client,
    _setup_cloud_device,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.esp_weaver.const import CONF_CONNECTION_MODE, CONF_NODE_ID
from custom_components.esp_weaver.data import DomainData, domain_data
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.intents import INTENT_NUMBER_SET_VALUE
from custom_components.esp_weaver.iot.specs.keys import CONNECTION_MODE_LOCAL
from custom_components.esp_weaver.profiles import DeviceIdentity

from .conftest import (
    NODE_PRODUCT_LABEL,
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    TEST_PORT,
    create_mock_config_entry,
    node_binding_options,
)

# What the bound product claims the node is, for the setup helpers called directly.
IDENTITY = DeviceIdentity(
    serial_number=TEST_NODE_ID,
    manufacturer="Espressif",
    model="Switch",
)


@pytest.fixture(autouse=True)
def _entries_can_set_up(node_product_in_store: None) -> None:
    """Every entry here is bound to the test node's product; keep its content stored."""


@contextmanager
def setup_entry_patches(
    hass: HomeAssistant,
    mock_esp_api: MagicMock,
    mock_coordinator: MagicMock,
    mock_event_dispatcher: MagicMock,
) -> Generator[None]:
    """Provide common patches for setup entry tests."""
    with (
        patch(
            "custom_components.esp_weaver._get_or_create_api",
            new_callable=AsyncMock,
            return_value=mock_esp_api,
        ),
        patch(
            "custom_components.esp_weaver.ESPDataUpdateCoordinator",
            return_value=mock_coordinator,
        ),
        patch(
            "custom_components.esp_weaver.helpers.event_dispatcher.create_event_dispatcher",
            return_value=mock_event_dispatcher,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
        patch("custom_components.esp_weaver._register_device"),  # Skip device registry
    ):
        yield


class TestSetupEntry:
    """Test async_setup_entry."""

    async def test_setup_entry_success(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test successful setup of config entry."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            result = await async_setup_entry(hass, entry)

            assert result is True
            assert entry.runtime_data == mock_coordinator

    async def test_setup_refreshes_the_repository_copy_once_in_the_background(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setup does not wait on the network; the first entry starts one refresh."""
        refreshed = AsyncMock(return_value=MagicMock(name="refreshed catalog"))
        monkeypatch.setattr(esp_weaver, "async_refresh_catalog", refreshed)
        synced = MagicMock()
        monkeypatch.setattr(esp_weaver, "async_sync_binding_issues", synced)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            for node_id in ("node-a", "node-b"):
                entry = create_mock_config_entry(
                    {**mock_config_entry_data, CONF_NODE_ID: node_id}
                )
                assert await async_setup_entry(hass, entry) is True
            await hass.async_block_till_done()

        refreshed.assert_awaited_once_with(hass, online=True)
        # Once per setup for the offline catalog, once more after the refresh.
        assert synced.call_count == 3
        assert refreshed.return_value in [c.args[1] for c in synced.call_args_list]

    @pytest.mark.parametrize(
        ("options", "reason"),
        [
            ({}, "profile_unbound"),
            (
                {"profile": {"id": "acme/gone", "source": "local", "hash": "0" * 16}},
                "profile_missing",
            ),
        ],
    )
    async def test_an_entry_whose_product_cannot_be_produced_fails_to_set_up(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
        options: dict[str, Any],
        reason: str,
    ) -> None:
        """No binding, or bound content that is gone: the device fails, visibly.

        Nothing is resolved in its place - not a file of the same name, not a
        shipped product. The error names the way out: remove and re-add.
        """
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.options = options

        with (
            setup_entry_patches(
                hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
            ),
            pytest.raises(ConfigEntryError) as caught,
        ):
            await async_setup_entry(hass, entry)

        assert caught.value.translation_key == reason
        assert entry.runtime_data is None

    async def test_setup_entry_device_timeout(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test setup continues even when device connection times out."""
        entry = create_mock_config_entry(mock_config_entry_data)

        # Simulate device timeout
        mock_esp_api.register_device = AsyncMock(side_effect=TimeoutError())

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            # Should still return True even on timeout
            result = await async_setup_entry(hass, entry)

            assert result is True
            # Verify register_device was called and raised TimeoutError
            mock_esp_api.register_device.assert_called_once()


class TestUnloadEntry:
    """Test async_unload_entry."""

    async def test_unload_entry_success(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Unloading an entry unregisters its device and shuts its coordinator down."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        # Set up domain data
        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry],
            ),
        ):
            result = await async_unload_entry(hass, entry)

            assert result is True
            mock_esp_api.unregister_device.assert_awaited_once_with(TEST_NODE_ID)
            mock_coordinator.async_shutdown.assert_awaited_once()

    async def test_unload_entry_platform_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test unload fails when platform unload fails."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await async_unload_entry(hass, entry)

            assert result is False


class TestDeviceRegistration:
    """Test device registration."""

    async def test_device_registered_on_setup(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test device is registered in device registry on setup."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_device_registry = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_api",
                new_callable=AsyncMock,
                return_value=mock_esp_api,
            ),
            patch(
                "custom_components.esp_weaver.ESPDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.esp_weaver.helpers.event_dispatcher.create_event_dispatcher",
                return_value=mock_event_dispatcher,
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.esp_weaver.dr.async_get",
                return_value=mock_device_registry,
            ),
        ):
            await async_setup_entry(hass, entry)

            # Registered with the serial number and the product picked: the
            # model id and firmware version are the node's, read by the
            # coordinator.
            mock_device_registry.async_get_or_create.assert_called_once()
            call_kwargs = mock_device_registry.async_get_or_create.call_args[1]
            assert call_kwargs["identifiers"] == {(DOMAIN, TEST_NODE_ID)}
            assert call_kwargs["name"] == TEST_DEVICE_NAME
            assert call_kwargs["serial_number"] == TEST_NODE_ID
            assert call_kwargs["manufacturer"] == "Espressif"
            assert call_kwargs["model"] == NODE_PRODUCT_LABEL
            assert not {"model_id", "sw_version"} & call_kwargs.keys()


class TestDeviceRemoval:
    """Deleting a device from the UI, which only an offline device allows.

    A device that still answers would be rediscovered on the next poll, so
    removing it would look like it had not worked.
    """

    def _device_entry(self) -> MagicMock:
        """Return a device registry entry for the node under test."""
        device_entry = MagicMock(spec=dr.DeviceEntry)
        device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}
        return device_entry

    async def test_an_offline_device_can_be_removed(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
    ) -> None:
        """Test removal is allowed once the node has stopped answering."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)
        domain_data(hass).api = mock_esp_api

        result = await esp_weaver.async_remove_config_entry_device(
            hass, entry, self._device_entry()
        )

        mock_esp_api.is_device_available_async.assert_called_once_with(TEST_NODE_ID)
        assert result is True

    async def test_an_online_device_is_kept(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
    ) -> None:
        """Test a node that still answers refuses to be removed."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.is_device_available_async = AsyncMock(return_value=True)
        domain_data(hass).api = mock_esp_api

        result = await esp_weaver.async_remove_config_entry_device(
            hass, entry, self._device_entry()
        )

        assert result is False

    @pytest.mark.parametrize("started", [True, False], ids=["no_api", "domain_absent"])
    async def test_removal_is_allowed_when_nothing_can_be_asked(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        started: bool,
    ) -> None:
        """Test an unanswerable question does not trap the device in the UI."""
        entry = create_mock_config_entry(mock_config_entry_data)
        hass.data.pop(DOMAIN, None)
        if started:
            domain_data(hass)

        result = await esp_weaver.async_remove_config_entry_device(
            hass, entry, self._device_entry()
        )

        assert result is True


class TestReloadProfilesService:
    """The service that re-reads the profile files."""

    def test_the_name_is_the_one_reload_all_sweeps(self) -> None:
        """Developer tools calls the service named `reload` on every domain."""
        assert SERVICE_RELOAD == "reload"

    async def test_loading_the_integration_is_enough_to_get_the_service(
        self, hass: HomeAssistant
    ) -> None:
        """It is the integration's, not a device's, and outlives every entry."""
        assert await async_setup(hass, {})

        assert hass.services.has_service(DOMAIN, SERVICE_RELOAD)

    async def test_the_intent_is_registered_once_for_the_run(
        self, hass: HomeAssistant
    ) -> None:
        """An intent cannot be unregistered, and registering it twice warns."""
        assert await async_setup(hass, {})

        assert INTENT_NUMBER_SET_VALUE in {
            handler.intent_type for handler in intent.async_get(hass)
        }

    def test_every_action_is_described_wherever_it_is_declared(self) -> None:
        """An action named in one file and not the other is half an action."""
        component = Path(esp_weaver.__file__).parent

        declared = yaml.safe_load((component / "services.yaml").read_text())
        described = json.loads((component / "strings.json").read_text())["services"]

        assert set(declared) == set(described)
        assert SERVICE_RELOAD in declared

        for name, body in declared.items():
            assert set(body["fields"]) == set(described[name]["fields"]), name

        assert set(declared[SERVICE_RELOAD]["fields"]) == {
            ATTR_URL,
            ATTR_FILENAME,
            ATTR_OVERWRITE,
        }

    async def test_calling_it_rereads_the_profiles(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """A fresh catalog is the whole point of calling it."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            await async_setup(hass, {})
            await async_setup_entry(hass, entry)
            before = domain_data(hass).catalog

            with patch.object(
                hass.config_entries, "async_loaded_entries", return_value=[]
            ):
                await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)

            assert domain_data(hass).catalog is not before

    async def test_it_reloads_the_entries_so_entities_follow(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """New profiles only reach the user once entities are rebuilt."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            await async_setup(hass, {})
            await async_setup_entry(hass, entry)

            with (
                patch.object(
                    hass.config_entries, "async_loaded_entries", return_value=[entry]
                ),
                patch.object(
                    hass.config_entries, "async_reload", new_callable=AsyncMock
                ) as reload,
            ):
                await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)

            reload.assert_awaited_once_with(entry.entry_id)

    async def test_a_non_admin_cannot_call_it(
        self, hass: HomeAssistant, hass_read_only_user: MockUser
    ) -> None:
        """It fetches a URL of the caller's choosing and writes the answer.

        Only enforced for signed-in users; automations are unaffected.
        """
        assert await async_setup(hass, {})

        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RELOAD,
                blocking=True,
                context=Context(user_id=hass_read_only_user.id),
            )


class TestReloadWithAUrl:
    """The same action, given a URL, fetches a profile before reloading."""

    async def test_a_plain_reload_downloads_nothing(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """The reload-all-YAML button calls this with no data at all."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            await async_setup(hass, {})
            await async_setup_entry(hass, entry)

            with (
                patch(
                    "custom_components.esp_weaver.async_import_profile",
                    new_callable=AsyncMock,
                ) as importer,
                patch.object(
                    hass.config_entries, "async_loaded_entries", return_value=[]
                ),
            ):
                result = await hass.services.async_call(
                    DOMAIN, SERVICE_RELOAD, blocking=True, return_response=True
                )

            importer.assert_not_awaited()
            # No file was written, so no path to report - just what is loaded.
            assert result is not None
            assert ATTR_FILE not in result
            profiles = result[ATTR_PROFILES]
            assert isinstance(profiles, list)
            assert "espressif/switch" in profiles

    async def test_it_imports_then_applies_and_returns_the_path(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """A file that landed but was not applied looks like nothing happened."""
        entry = create_mock_config_entry(mock_config_entry_data)
        url = "https://example.com/acme.yaml"

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            await async_setup(hass, {})
            await async_setup_entry(hass, entry)
            before = domain_data(hass).catalog

            with (
                patch(
                    "custom_components.esp_weaver.async_import_profile",
                    new_callable=AsyncMock,
                    return_value=Path("/config/esp_weaver/acme.yaml"),
                ) as importer,
                patch.object(
                    hass.config_entries, "async_loaded_entries", return_value=[]
                ),
            ):
                result = await hass.services.async_call(
                    DOMAIN,
                    SERVICE_RELOAD,
                    {ATTR_URL: url},
                    blocking=True,
                    return_response=True,
                )

            importer.assert_awaited_once_with(hass, url, None, False)
            assert result is not None
            assert result[ATTR_FILE] == "/config/esp_weaver/acme.yaml"
            assert domain_data(hass).catalog is not before

    async def test_something_that_is_not_a_url_is_refused_up_front(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """A local path is the mistake to expect, and it is not fetchable."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            await async_setup(hass, {})
            await async_setup_entry(hass, entry)

            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_RELOAD,
                    {ATTR_URL: "/home/pi/acme.yaml"},
                    blocking=True,
                )

    async def test_a_filename_without_a_url_is_a_half_filled_form(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Reloading anyway would look like the import worked."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            await async_setup(hass, {})
            await async_setup_entry(hass, entry)

            with pytest.raises(
                ServiceValidationError, match="reload_filename_without_url"
            ):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_RELOAD,
                    {ATTR_FILENAME: "acme.yaml"},
                    blocking=True,
                )


class TestIntegrationCleanup:
    """Test integration cleanup when all entries are removed."""

    async def test_cleanup_on_last_entry_unload(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test API cleanup when last entry is unloaded."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry],  # Only this entry exists
            ),
        ):
            await async_unload_entry(hass, entry)

            # API should be cleaned up
            mock_esp_api.cleanup.assert_called_once()
            # Domain data should be removed
            assert DOMAIN not in hass.data

    async def test_unloading_one_of_several_forgets_only_its_own_binding(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """The other device's bookkeeping, and the shared API, stay put."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        other = create_mock_config_entry(entry_id="other", unique_id="other_node")

        data = domain_data(hass)
        data.api = mock_esp_api
        data.loaded_bindings = {entry.entry_id: None, "other": None}

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry, other]
            ),
        ):
            await async_unload_entry(hass, entry)

        assert domain_data(hass).loaded_bindings == {"other": None}
        # The other device is still set up, so its API is left alone.
        mock_esp_api.cleanup.assert_not_called()

    async def test_the_services_survive_the_last_device_going_away(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Deleting a device left no way to fix a profile and add it back."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        await async_setup(hass, {})
        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(hass.config_entries, "async_entries", return_value=[entry]),
        ):
            await async_unload_entry(hass, entry)

        assert hass.services.has_service(DOMAIN, SERVICE_RELOAD)
        assert INTENT_NUMBER_SET_VALUE in {
            handler.intent_type for handler in intent.async_get(hass)
        }

    async def test_no_cleanup_with_remaining_entries(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test no cleanup when other entries remain."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        other_entry = create_mock_config_entry(
            entry_id="other_entry",
            unique_id="other_node",
        )

        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry, other_entry],  # Multiple entries
            ),
        ):
            await async_unload_entry(hass, entry)

            # API should NOT be cleaned up
            mock_esp_api.cleanup.assert_not_called()
            # Domain data should still exist
            assert DOMAIN in hass.data

    async def test_an_entry_that_never_set_up_does_not_keep_the_api_alive(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """An entry in setup-retry holds nothing; the last loaded one cleans up."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        retrying = create_mock_config_entry(entry_id="retrying", unique_id="other")
        retrying.state = ConfigEntryState.SETUP_RETRY

        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry, retrying],
            ),
        ):
            await async_unload_entry(hass, entry)

        mock_esp_api.cleanup.assert_called_once()

    async def test_the_coordinator_stops_before_its_device_is_forgotten(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """A reconnection still running must not find the registry emptied."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        order: list[str] = []
        mock_coordinator.async_shutdown = AsyncMock(
            side_effect=lambda: order.append("shutdown")
        )
        mock_esp_api.unregister_device = AsyncMock(
            side_effect=lambda _node: order.append("unregister")
        )

        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(hass.config_entries, "async_entries", return_value=[entry]),
        ):
            await async_unload_entry(hass, entry)

        assert order == ["shutdown", "unregister"]


class TestSetupEntryEdgeCases:
    """Test async_setup_entry edge cases."""

    async def test_setup_entry_connection_error(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test handling of OSError/ConnectionError during device registration."""
        entry = create_mock_config_entry(mock_config_entry_data)

        # Simulate OSError during device registration
        mock_esp_api.register_device = AsyncMock(
            side_effect=OSError("Network unreachable")
        )

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            # Should still succeed even with connection error
            result = await async_setup_entry(hass, entry)

            assert result is True
            # Verify register_device was called and raised OSError
            mock_esp_api.register_device.assert_called_once()

    async def test_setup_entry_client_error(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test handling of aiohttp ClientError during device registration."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_esp_api.register_device = AsyncMock(
            side_effect=ClientError("Connection failed")
        )

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            result = await async_setup_entry(hass, entry)

            assert result is True
            # Verify register_device was called and raised ClientError
            mock_esp_api.register_device.assert_called_once()

    async def test_setup_entry_first_refresh_update_failed(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test handling of UpdateFailed during first refresh."""
        entry = create_mock_config_entry(mock_config_entry_data)

        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("Device offline")
        )

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            # Should still succeed even with UpdateFailed
            result = await async_setup_entry(hass, entry)

            assert result is True


class TestGetOrCreateApi:
    """Test _get_or_create_api function."""

    async def test_get_existing_api(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test getting existing API from domain data."""
        data = DomainData(api=mock_esp_api)

        result = await _get_or_create_api(hass, data)

        assert result == mock_esp_api

    async def test_create_new_api(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test creating new API when none exists."""
        data = DomainData()

        with (
            patch("custom_components.esp_weaver.ESPWeaverApi") as mock_api_class,
            patch(
                "custom_components.esp_weaver.create_event_dispatcher"
            ) as mock_create_dispatcher,
        ):
            mock_api_instance = MagicMock()
            mock_api_class.return_value = mock_api_instance
            mock_create_dispatcher.return_value = MagicMock()

            result = await _get_or_create_api(hass, data)

            assert result == mock_api_instance
            assert data.api == mock_api_instance

    async def test_the_api_resolves_each_node_against_its_own_entry(
        self, hass: HomeAssistant
    ) -> None:
        """The product a node is bound to is the one its entities come from."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="bound",
            data={CONF_HOST: TEST_HOST, CONF_PORT: TEST_PORT, CONF_NODE_ID: "bound"},
            options=node_binding_options(),
        )
        entry.add_to_hass(hass)
        orphan = MockConfigEntry(
            domain=DOMAIN,
            unique_id="orphan",
            data={CONF_HOST: TEST_HOST, CONF_PORT: TEST_PORT, CONF_NODE_ID: "orphan"},
            options={"profile": {"id": "acme/gone", "source": "local", "hash": "x"}},
        )
        orphan.add_to_hass(hass)
        # A lightbulb, which the bound entry's product declares.
        light = {
            "devices": [
                {
                    "name": "Light",
                    "type": "esp.device.lightbulb",
                    "params": [
                        {
                            "name": "Power",
                            "type": "esp.param.power",
                            "data_type": "bool",
                            "properties": ["read", "write"],
                        }
                    ],
                }
            ]
        }

        with (
            patch("custom_components.esp_weaver.ESPWeaverApi") as mock_api_class,
            patch("custom_components.esp_weaver.create_event_dispatcher"),
        ):
            await _get_or_create_api(hass, DomainData())
            resolve = mock_api_class.call_args.kwargs["resolve_bindings"]

            bound = await resolve("bound", light, {})
            unknown = await resolve("nobody", light, {})
            # Its setup already failed on the same error; a report that still
            # arrives announces nothing rather than resolving against a stand-in.
            orphaned = await resolve("orphan", light, {})

        assert [binding.key for binding in bound] == ["lightbulb"]
        assert unknown == []
        assert orphaned == []


class TestCleanupIntegration:
    """Test _cleanup_integration function."""

    async def test_cleanup_with_api(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cleanup calls api.cleanup."""
        domain_data(hass).api = mock_esp_api

        await _cleanup_integration(hass, mock_esp_api)

        mock_esp_api.cleanup.assert_called_once()
        assert DOMAIN not in hass.data

    async def test_cleanup_without_api(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test cleanup handles None API gracefully."""
        domain_data(hass)

        # Should not raise
        await _cleanup_integration(hass, None)

        assert DOMAIN not in hass.data


class TestCloudDeviceSetup:
    """Test cloud device setup."""

    async def test_setup_cloud_device_no_client(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cloud device setup fails when no CLI client."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                side_effect=ConfigEntryNotReady("no session"),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, data, IDENTITY
            )


class TestGetOrCreateCliClient:
    """Test _get_or_create_cli_client function."""

    async def test_reuse_existing_client(self) -> None:
        """Test reusing existing CLI client."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        existing_client = MagicMock()
        data = DomainData(cloud_clients={"global": existing_client})

        result = await _get_or_create_cli_client(data, entry)

        assert result == existing_client

    async def test_create_new_client_success(self) -> None:
        """Test creating a new CLI client when none exists."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        mock_client = MagicMock()
        mock_client.check_health = AsyncMock(return_value=True)

        with patch(
            "custom_components.esp_weaver.RainMakerCLIClient",
            return_value=mock_client,
        ):
            result = await _get_or_create_cli_client(data, entry)

        assert result == mock_client
        assert data.cloud_clients["global"] == mock_client

    async def test_create_new_client_health_check_fails(self) -> None:
        """Test setup is deferred when the health check fails."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "china",
            },
        )

        data = DomainData()

        mock_client = MagicMock()
        mock_client.check_health = AsyncMock(return_value=False)

        with (
            patch(
                "custom_components.esp_weaver.RainMakerCLIClient",
                return_value=mock_client,
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await _get_or_create_cli_client(data, entry)

        assert "china" not in data.cloud_clients


class TestSetupCloudDeviceFullPath:
    """Test _setup_cloud_device full success path."""

    async def test_setup_cloud_device_success(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test full cloud device setup success path."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        mock_cloud_client = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                return_value=mock_cloud_client,
            ),
            patch(
                "custom_components.esp_weaver.ESPDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.esp_weaver._register_device"),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, data, IDENTITY
            )

        assert result is True
        mock_esp_api.set_cloud_client.assert_called_once_with(
            TEST_NODE_ID, mock_cloud_client
        )
        mock_esp_api.register_cloud_device.assert_called_once_with(TEST_NODE_ID)
        mock_coordinator.async_config_entry_first_refresh.assert_called_once()

    async def test_setup_cloud_device_discovery_exception(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cloud device setup handles discovery exception gracefully."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        mock_cloud_client = MagicMock()
        mock_cloud_client.get_node_details = AsyncMock(
            side_effect=RuntimeError("cloud error")
        )

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                return_value=mock_cloud_client,
            ),
            patch(
                "custom_components.esp_weaver.ESPDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.esp_weaver._register_device"),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, data, IDENTITY
            )

        assert result is True
        mock_esp_api.mark_discovery_completed.assert_not_called()

    async def test_setup_cloud_device_no_config_after_retries(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cloud device setup when no config available after retries."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        mock_cloud_client = MagicMock()
        mock_cloud_client.get_node_details = AsyncMock(return_value={"info": {}})

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                return_value=mock_cloud_client,
            ),
            patch(
                "custom_components.esp_weaver.ESPDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.esp_weaver._register_device"),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, data, IDENTITY
            )

        assert result is True
        mock_esp_api.discover_from_cloud.assert_not_called()

    async def test_setup_cloud_device_first_refresh_not_ready(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """A first refresh that is not ready leaves the entry loaded.

        Raising would leave the forwarded platforms in place for the retry to
        forward again; the coordinator keeps polling either way.
        """
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        mock_cloud_client = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("not ready")
        )

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                return_value=mock_cloud_client,
            ),
            patch(
                "custom_components.esp_weaver.ESPDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.esp_weaver._register_device"),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, data, IDENTITY
            )

        assert result is True

    async def test_setup_cloud_device_first_refresh_update_failed(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """A failed first refresh leaves the entry loaded, as above."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        data = DomainData()

        mock_cloud_client = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("failed")
        )

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                return_value=mock_cloud_client,
            ),
            patch(
                "custom_components.esp_weaver.ESPDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch("custom_components.esp_weaver._register_device"),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, data, IDENTITY
            )

        assert result is True


class TestUnloadEntryErrorPaths:
    """Test error paths in async_unload_entry."""

    async def test_unload_entry_unregister_error(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test unload handles unregister_device error."""
        entry = create_mock_config_entry(
            data={CONF_NODE_ID: TEST_NODE_ID},
        )

        mock_coordinator = MagicMock()
        mock_coordinator.async_shutdown = AsyncMock()
        entry.runtime_data = mock_coordinator

        mock_esp_api.unregister_device = AsyncMock(
            side_effect=OSError("disconnect error")
        )
        mock_esp_api.cleanup = AsyncMock()

        domain_data(hass).api = mock_esp_api

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry],
            ),
        ):
            result = await async_unload_entry(hass, entry)

        assert result is True
        mock_coordinator.async_shutdown.assert_called_once()
        mock_esp_api.cleanup.assert_called_once()


class TestSetupLocalDeviceFirstRefresh:
    """Test _setup_local_device first refresh error paths."""

    async def test_local_device_first_refresh_not_ready(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
        mock_coordinator: MagicMock,
        mock_event_dispatcher: MagicMock,
    ) -> None:
        """Test local device setup handles ConfigEntryNotReady gracefully."""
        entry = create_mock_config_entry(
            data={
                CONF_HOST: TEST_HOST,
                CONF_PORT: TEST_PORT,
                CONF_NODE_ID: TEST_NODE_ID,
                CONF_CONNECTION_MODE: CONNECTION_MODE_LOCAL,
            },
        )

        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("not ready")
        )

        with setup_entry_patches(
            hass, mock_esp_api, mock_coordinator, mock_event_dispatcher
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
