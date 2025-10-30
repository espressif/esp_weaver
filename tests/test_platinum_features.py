# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for ESP-Weaver Platinum quality features."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.esp_weaver import async_remove_config_entry_device
from custom_components.esp_weaver.imu_gesture import ESPWeaverGestureSensor
from custom_components.esp_weaver.interactive_input import ESPWeaverInputSensor
from custom_components.esp_weaver.iot.specs.events import DOMAIN
from custom_components.esp_weaver.low_power_sleep import ESPWeaverSleepSensor
from custom_components.esp_weaver.number import ESPWeaverThresholdNumber

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestStaleDeviceRemoval:
    """Test stale device removal."""

    async def test_remove_offline_device(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
    ) -> None:
        """Test removing offline device is allowed."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.is_device_available = MagicMock(return_value=False)

        hass.data[DOMAIN] = {"api": mock_esp_api}

        device_entry = MagicMock(spec=dr.DeviceEntry)
        device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}

        result = await async_remove_config_entry_device(hass, entry, device_entry)

        mock_esp_api.is_device_available.assert_called_once_with(TEST_NODE_ID)
        assert result is True

    async def test_remove_online_device_blocked(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_esp_api: MagicMock,
    ) -> None:
        """Test removing online device is blocked."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_esp_api.is_device_available = MagicMock(return_value=True)

        hass.data[DOMAIN] = {"api": mock_esp_api}

        device_entry = MagicMock(spec=dr.DeviceEntry)
        device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}

        result = await async_remove_config_entry_device(hass, entry, device_entry)

        mock_esp_api.is_device_available.assert_called_once_with(TEST_NODE_ID)
        assert result is False

    async def test_remove_device_no_api(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test removing device when API not available."""
        entry = create_mock_config_entry(mock_config_entry_data)

        # No API in domain data
        hass.data[DOMAIN] = {}

        device_entry = MagicMock(spec=dr.DeviceEntry)
        device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}

        result = await async_remove_config_entry_device(hass, entry, device_entry)

        # Should allow removal when API not available
        assert result is True

    async def test_remove_device_domain_absent(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test removing device when DOMAIN is entirely absent from hass.data."""
        entry = create_mock_config_entry(mock_config_entry_data)

        # Ensure DOMAIN is not in hass.data
        if DOMAIN in hass.data:
            del hass.data[DOMAIN]

        device_entry = MagicMock(spec=dr.DeviceEntry)
        device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}

        result = await async_remove_config_entry_device(hass, entry, device_entry)

        # Should allow removal when domain key is missing
        assert result is True


class TestEntityCategories:
    """Test entity categories are correctly set."""

    def test_threshold_number_entity_category(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test threshold number has CONFIG category."""
        entity = ESPWeaverThresholdNumber(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_type="temperature",
            threshold_type="min",
        )

        assert getattr(entity, "_attr_entity_category", None) == EntityCategory.CONFIG

    @pytest.mark.parametrize(
        ("entity_class", "entity_name"),
        [
            (ESPWeaverGestureSensor, "gesture sensor"),
            (ESPWeaverInputSensor, "input sensor"),
            (ESPWeaverSleepSensor, "sleep sensor"),
        ],
    )
    def test_primary_sensor_no_entity_category(
        self,
        mock_coordinator: MagicMock,
        entity_class: type,
        entity_name: str,
    ) -> None:
        """Test primary sensors have no entity category."""
        entity = entity_class(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        # Primary entities have no entity category
        assert getattr(entity, "_attr_entity_category", None) is None, (
            f"{entity_name} should not have an entity category"
        )
