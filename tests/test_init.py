# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver integration setup and unload."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.esp_weaver import (
    _cleanup_integration,
    _get_or_create_api,
    _get_or_create_cli_client,
    _setup_cloud_device,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.esp_weaver.iot.specs.events import DOMAIN
from custom_components.esp_weaver.iot.specs.keys import CONF_NODE_ID, KEY_API

from .conftest import (
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    TEST_PORT,
    create_mock_config_entry,
)


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
        patch("custom_components.esp_weaver.battery_energy.setup_discovery_listener"),
        patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
        patch(
            "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
        ),
        patch("custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"),
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

    async def test_setup_entry_missing_node_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test setup fails with missing node_id."""
        entry = create_mock_config_entry(
            data={
                CONF_HOST: TEST_HOST,
                CONF_PORT: TEST_PORT,
                # Missing CONF_NODE_ID
            },
        )

        result = await async_setup_entry(hass, entry)

        assert result is False

    async def test_setup_entry_missing_host(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test setup fails with missing host."""
        entry = create_mock_config_entry(
            data={
                # Missing CONF_HOST
                CONF_PORT: TEST_PORT,
                CONF_NODE_ID: TEST_NODE_ID,
            },
        )

        result = await async_setup_entry(hass, entry)

        assert result is False

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
        """Test successful unload of config entry.

        Verifies per-entry cleanup when async_unload_entry is called:
        - Calls mock_esp_api.unregister_device to remove the device from API
        - Calls mock_coordinator.async_shutdown to clean up coordinator resources

        This test uses a single-entry scenario (async_entries returns [entry]),
        which also triggers integration-level cleanup (api.cleanup()).
        For tests of integration cleanup specifically, see
        TestIntegrationCleanup.test_cleanup_on_last_entry_unload.
        """
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        # Set up domain data
        hass.data[DOMAIN] = {"api": mock_esp_api}

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
            patch(
                "custom_components.esp_weaver.battery_energy.setup_discovery_listener"
            ),
            patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
            patch(
                "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
            ),
            patch(
                "custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"
            ),
        ):
            await async_setup_entry(hass, entry)

            # Verify device was registered
            mock_device_registry.async_get_or_create.assert_called_once()
            call_kwargs = mock_device_registry.async_get_or_create.call_args[1]
            assert call_kwargs["identifiers"] == {(DOMAIN, TEST_NODE_ID)}
            assert call_kwargs["name"] == TEST_DEVICE_NAME


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

        hass.data[DOMAIN] = {"api": mock_esp_api}

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

        hass.data[DOMAIN] = {"api": mock_esp_api}

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
        domain_data = {KEY_API: mock_esp_api}

        result = await _get_or_create_api(hass, domain_data)

        assert result == mock_esp_api

    async def test_create_new_api(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test creating new API when none exists."""
        domain_data: dict[str, Any] = {}

        with (
            patch("custom_components.esp_weaver.ESPWeaverApi") as mock_api_class,
            patch(
                "custom_components.esp_weaver.create_event_dispatcher"
            ) as mock_create_dispatcher,
        ):
            mock_api_instance = MagicMock()
            mock_api_class.return_value = mock_api_instance
            mock_create_dispatcher.return_value = MagicMock()

            result = await _get_or_create_api(hass, domain_data)

            assert result == mock_api_instance
            assert domain_data[KEY_API] == mock_api_instance


class TestCleanupIntegration:
    """Test _cleanup_integration function."""

    async def test_cleanup_with_api(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cleanup calls api.cleanup."""
        hass.data[DOMAIN] = {"api": mock_esp_api}

        await _cleanup_integration(hass, mock_esp_api)

        mock_esp_api.cleanup.assert_called_once()
        assert DOMAIN not in hass.data

    async def test_cleanup_without_api(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test cleanup handles None API gracefully."""
        hass.data[DOMAIN] = {}

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

        domain_data: dict[str, Any] = {}

        with (
            patch(
                "custom_components.esp_weaver._get_or_create_cli_client",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, domain_data
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
        # Key is "cli_client_{region}"
        domain_data = {"cli_client_global": existing_client}

        result = await _get_or_create_cli_client(domain_data, entry)

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

        domain_data: dict[str, Any] = {}

        mock_client = MagicMock()
        mock_client.check_health = AsyncMock(return_value=True)

        with patch(
            "custom_components.esp_weaver.RainMakerCLIClient",
            return_value=mock_client,
        ):
            result = await _get_or_create_cli_client(domain_data, entry)

        assert result == mock_client
        assert domain_data["cli_client_global"] == mock_client

    async def test_create_new_client_health_check_fails(self) -> None:
        """Test returns None when health check fails."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "china",
            },
        )

        domain_data: dict[str, Any] = {}

        mock_client = MagicMock()
        mock_client.check_health = AsyncMock(return_value=False)

        with patch(
            "custom_components.esp_weaver.RainMakerCLIClient",
            return_value=mock_client,
        ):
            result = await _get_or_create_cli_client(domain_data, entry)

        assert result is None
        assert "cli_client_china" not in domain_data


class TestAsyncMigrateEntry:
    """Test async_migrate_entry."""

    async def test_migrate_future_version(self, hass: HomeAssistant) -> None:
        """Test migration rejects future versions."""
        entry = create_mock_config_entry(
            data={CONF_NODE_ID: TEST_NODE_ID},
        )
        entry.version = 2
        entry.minor_version = 0

        result = await async_migrate_entry(hass, entry)

        assert result is False

    async def test_migrate_current_version(self, hass: HomeAssistant) -> None:
        """Test no migration needed for current version."""
        entry = create_mock_config_entry(
            data={CONF_NODE_ID: TEST_NODE_ID},
        )
        entry.version = 1
        entry.minor_version = 1

        result = await async_migrate_entry(hass, entry)

        assert result is True

    async def test_migrate_old_version(self, hass: HomeAssistant) -> None:
        """Test migration from old version 1.0 to 1.1."""
        entry = create_mock_config_entry(
            data={CONF_NODE_ID: TEST_NODE_ID},
        )
        entry.version = 1
        entry.minor_version = 0

        with patch.object(hass.config_entries, "async_update_entry") as mock_update:
            result = await async_migrate_entry(hass, entry)

        assert result is True
        mock_update.assert_called_once_with(entry, version=1, minor_version=1)


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

        domain_data: dict[str, Any] = {}

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
            patch(
                "custom_components.esp_weaver.battery_energy.setup_discovery_listener"
            ),
            patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
            patch(
                "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
            ),
            patch(
                "custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"
            ),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, domain_data
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

        domain_data: dict[str, Any] = {}

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
            patch(
                "custom_components.esp_weaver.battery_energy.setup_discovery_listener"
            ),
            patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
            patch(
                "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
            ),
            patch(
                "custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"
            ),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, domain_data
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

        domain_data: dict[str, Any] = {}

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
            patch(
                "custom_components.esp_weaver.battery_energy.setup_discovery_listener"
            ),
            patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
            patch(
                "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
            ),
            patch(
                "custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, domain_data
            )

        assert result is True
        mock_esp_api.discover_from_cloud.assert_not_called()

    async def test_setup_cloud_device_first_refresh_not_ready(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cloud setup propagates ConfigEntryNotReady on first refresh."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        domain_data: dict[str, Any] = {}

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
            patch(
                "custom_components.esp_weaver.battery_energy.setup_discovery_listener"
            ),
            patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
            patch(
                "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
            ),
            patch(
                "custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, domain_data
            )

    async def test_setup_cloud_device_first_refresh_update_failed(
        self,
        hass: HomeAssistant,
        mock_esp_api: MagicMock,
    ) -> None:
        """Test cloud setup propagates UpdateFailed on first refresh."""
        entry = create_mock_config_entry(
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "connection_mode": "cloud",
                "rainmaker_region": "global",
            },
        )

        domain_data: dict[str, Any] = {}

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
            patch(
                "custom_components.esp_weaver.battery_energy.setup_discovery_listener"
            ),
            patch("custom_components.esp_weaver.imu_gesture.setup_discovery_listener"),
            patch(
                "custom_components.esp_weaver.interactive_input.setup_discovery_listener"
            ),
            patch(
                "custom_components.esp_weaver.low_power_sleep.setup_discovery_listener"
            ),
            pytest.raises(UpdateFailed),
        ):
            await _setup_cloud_device(
                hass, entry, mock_esp_api, TEST_NODE_ID, domain_data
            )


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

        hass.data[DOMAIN] = {KEY_API: mock_esp_api}

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
