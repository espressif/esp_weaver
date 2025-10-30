# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver base entity class."""

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.entity import ESPWeaverBaseEntity
from custom_components.esp_weaver.iot.specs.device_specs import DEFAULT_MANUFACTURER
from custom_components.esp_weaver.iot.specs.events import DOMAIN

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID


class TestESPWeaverBaseEntity:
    """Test ESPWeaverBaseEntity class."""

    def test_entity_initialization(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic entity initialization."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
            device_name=TEST_DEVICE_NAME,
        )

        assert entity._node_id == TEST_NODE_ID
        assert entity._device_name == TEST_DEVICE_NAME
        assert entity._attr_has_entity_name is True
        assert entity._attr_should_poll is False

    def test_entity_unique_id(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity unique_id generation."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            entity_key="test_key",
        )

        expected_unique_id = f"{DOMAIN}_{TEST_NODE_ID}_test_key"
        assert entity._attr_unique_id == expected_unique_id

    def test_entity_unique_id_without_key(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity without entity_key has no unique_id set."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        # unique_id should not be set when no entity_key
        assert getattr(entity, "_attr_unique_id", None) is None

    def test_entity_device_info(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity device_info generation."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            model=f"ESP-{TEST_NODE_ID}",
        )

        device_info = entity._attr_device_info
        assert device_info is not None
        assert (DOMAIN, TEST_NODE_ID) in device_info["identifiers"]
        assert device_info["name"] == TEST_DEVICE_NAME
        assert device_info["manufacturer"] == DEFAULT_MANUFACTURER
        assert device_info["model"] == f"ESP-{TEST_NODE_ID}"

    def test_entity_uses_coordinator_device_name(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity uses coordinator's device_name when not provided."""
        mock_coordinator.device_name = "Coordinator Device Name"

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            # No device_name provided
        )

        assert entity._device_name == "Coordinator Device Name"

    def test_entity_coordinator_relationship(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity coordinator is properly set."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert entity.coordinator is mock_coordinator


class TestEntityAvailability:
    """Test entity availability logic."""

    def test_entity_available_when_coordinator_available(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity is available when coordinator is available."""
        mock_coordinator.last_update_success = True
        mock_coordinator.is_available = True

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert entity.available is True

    def test_entity_unavailable_when_update_failed(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity is unavailable when coordinator update failed."""
        mock_coordinator.last_update_success = False
        mock_coordinator.is_available = True

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert entity.available is False

    def test_entity_unavailable_when_device_offline(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity is unavailable when device is offline."""
        mock_coordinator.last_update_success = True
        mock_coordinator.is_available = False

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert entity.available is False

    def test_entity_unavailable_when_both_false(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test entity is unavailable when both conditions are false."""
        mock_coordinator.last_update_success = False
        mock_coordinator.is_available = False

        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )

        assert entity.available is False


class TestEntityCoordinatorUpdate:
    """Test entity coordinator update handling."""

    async def test_handle_coordinator_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test _handle_coordinator_update calls async_write_ha_state."""
        entity = ESPWeaverBaseEntity(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
        )
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        entity._handle_coordinator_update()

        entity.async_write_ha_state.assert_called_once()
