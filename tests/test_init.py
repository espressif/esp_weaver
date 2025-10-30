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
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.esp_weaver import (
    _cleanup_integration,
    _get_or_create_api,
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
        domain_data = {}

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
