# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver helpers discovery module."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.helpers.discovery import (
    PLATFORM_CACHE_KEYS,
    DiscoveryConfig,
    PlatformSetupResult,
    create_discovery_listener,
    setup_platform_discovery,
    setup_single_entity_discovery,
)
from custom_components.esp_weaver.iot.specs.events import DOMAIN
from custom_components.esp_weaver.iot.specs.keys import CONF_NODE_ID, KEY_DEVICE_NAME

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestDiscoveryConfig:
    """Test DiscoveryConfig dataclass."""

    def test_basic_creation(self) -> None:
        """Test basic DiscoveryConfig creation."""
        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
        )

        assert config.discovered_event == "esp_weaver_test_discovered"
        assert config.entity_id_suffix == "test_entity"
        assert config.entity_name == "Test Entity"  # Auto-generated

    def test_custom_entity_name(self) -> None:
        """Test DiscoveryConfig with custom entity name."""
        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
            entity_name="Custom Name",
        )

        assert config.entity_name == "Custom Name"

    def test_with_extra_kwargs_builder(self) -> None:
        """Test DiscoveryConfig with extra kwargs builder."""

        def extra_builder(data: dict) -> dict:
            return {"extra": data.get("value", 0)}

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
            extra_entity_kwargs=extra_builder,
        )

        assert config.extra_entity_kwargs is not None
        result = config.extra_entity_kwargs({"value": 42})
        assert result == {"extra": 42}


class TestCreateDiscoveryListener:
    """Test create_discovery_listener function."""

    async def test_creates_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that create_discovery_listener creates event listener.

        Note: Handler behavior (event processing, entity creation) is thoroughly
        tested in TestCreateDiscoveryListenerEventHandler.
        """
        entry = create_mock_config_entry(mock_config_entry_data)

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
        )

        # Should not raise
        create_discovery_listener(hass, TEST_NODE_ID, entry, config)


class TestSetupPlatformDiscovery:
    """Test setup_platform_discovery function."""

    async def test_returns_none_without_runtime_data(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test returns None when runtime_data is missing."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=MagicMock(),
            platform_name="sensor",
        )

        assert result is None

    async def test_returns_result_with_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test returns result when coordinator is available."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.node_id = TEST_NODE_ID

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=MagicMock(),
            platform_name="sensor",
        )

        assert result is not None
        assert result.node_id == TEST_NODE_ID
        assert result.coordinator == mock_coordinator

    async def test_result_has_discovered_entities(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test result contains discovered_entities dict."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.node_id = TEST_NODE_ID

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=MagicMock(),
            platform_name="sensor",
        )

        assert hasattr(result, "discovered_entities")
        assert isinstance(result.discovered_entities, dict)


class TestDiscoveryConfigPostInit:
    """Test DiscoveryConfig post initialization."""

    def test_underscore_to_title(self) -> None:
        """Test that underscores are converted to title case."""
        config = DiscoveryConfig(
            discovered_event="test",
            entity_id_suffix="battery_energy_sensor",
            entity_class=MagicMock,
        )

        assert config.entity_name == "Battery Energy Sensor"

    def test_single_word(self) -> None:
        """Test single word suffix."""
        config = DiscoveryConfig(
            discovered_event="test",
            entity_id_suffix="sensor",
            entity_class=MagicMock,
        )

        assert config.entity_name == "Sensor"

    def test_preserves_custom_name(self) -> None:
        """Test that custom name is preserved."""
        config = DiscoveryConfig(
            discovered_event="test",
            entity_id_suffix="sensor",
            entity_class=MagicMock,
            entity_name="My Custom Sensor",
        )

        assert config.entity_name == "My Custom Sensor"


class TestPlatformSetupResult:
    """Test PlatformSetupResult class."""

    def test_creation(self, mock_coordinator: MagicMock) -> None:
        """Test PlatformSetupResult creation."""
        async_add_entities = MagicMock()
        discovered_entities = {}

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
        )

        assert result.coordinator == mock_coordinator
        assert result.node_id == TEST_NODE_ID
        assert result.discovered_entities == discovered_entities
        assert result.async_add_entities == async_add_entities


class TestPlatformCacheKeys:
    """Test PLATFORM_CACHE_KEYS mapping."""

    @pytest.mark.parametrize(
        ("platform", "expected_key"),
        [
            ("sensor", "sensors"),
            ("binary_sensor", "binary_sensors"),
            ("light", "lights"),
            ("number", "numbers"),
        ],
    )
    def test_cache_key(self, platform: str, expected_key: str) -> None:
        """Test platform cache key mapping."""
        assert PLATFORM_CACHE_KEYS.get(platform) == expected_key


class TestSetupPlatformDiscoveryMissingNodeId:
    """Test setup_platform_discovery with missing node_id."""

    async def test_returns_none_without_node_id(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test returns None when node_id is missing."""
        entry = MagicMock()
        entry.runtime_data = mock_coordinator
        entry.data = {}  # No node_id

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=MagicMock(),
            platform_name="sensor",
        )

        assert result is None


class TestSetupSingleEntityDiscovery:
    """Test setup_single_entity_discovery function."""

    async def test_registers_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test that listener is registered.

        Note: Handler behavior (event processing, entity creation) is thoroughly
        tested in TestSetupSingleEntityDiscoveryEventHandler.
        """
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities={},
            async_add_entities=MagicMock(),
        )

        def entity_factory(event_data, coordinator, node_id, device_name):
            return MagicMock()

        # Should not raise
        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_test_discovered",
            platform_name="test",
            entity_factory=entity_factory,
        )

        # Verify async_on_unload was called (listener registered)
        entry.async_on_unload.assert_called()


class TestSetupPlatformDiscoveryStoresCallback:
    """Test setup_platform_discovery stores callback."""

    async def test_stores_callback_in_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test callback is stored in coordinator."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        setup_platform_discovery(
            config_entry=entry,
            async_add_entities=async_add_entities,
            platform_name="sensor",
        )

        assert mock_coordinator.entity_callbacks.get("sensor") == async_add_entities

    async def test_creates_discovered_entities_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test discovered_entities cache is created."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}

        setup_platform_discovery(
            config_entry=entry,
            async_add_entities=MagicMock(),
            platform_name="sensor",
        )

        assert "sensors" in mock_coordinator.discovered_entities

    async def test_uses_custom_platform_name_as_key(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test custom platform name is used as cache key."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}

        setup_platform_discovery(
            config_entry=entry,
            async_add_entities=MagicMock(),
            platform_name="custom_platform",  # Not in PLATFORM_CACHE_KEYS
        )

        # Should use platform_name as key when not in mapping
        assert "custom_platform" in mock_coordinator.discovered_entities


class TestCreateDiscoveryListenerEventHandler:
    """Test create_discovery_listener event handler execution."""

    async def test_handler_ignores_other_node_id(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler ignores events for other node IDs."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        mock_add_entities = MagicMock()
        mock_coordinator.entity_callbacks = {"sensor": mock_add_entities}

        mock_entity_class = MagicMock(return_value=MagicMock())

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event for different node
        hass.bus.async_fire(
            "esp_weaver_test_discovered",
            {CONF_NODE_ID: "different_node", "initial_data": {}},
        )
        await hass.async_block_till_done()

        # Entity should NOT be created
        mock_entity_class.assert_not_called()

    async def test_handler_creates_and_adds_entity(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler creates entity and adds to HA."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        mock_add_entities = MagicMock()
        mock_coordinator.entity_callbacks = {"sensor": mock_add_entities}

        mock_entity = MagicMock()
        mock_entity_class = MagicMock(return_value=mock_entity)

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event for correct node
        hass.bus.async_fire(
            "esp_weaver_test_discovered",
            {CONF_NODE_ID: TEST_NODE_ID, "initial_data": {"value": 42}},
        )
        await hass.async_block_till_done()

        # Entity should be created with correct kwargs
        mock_entity_class.assert_called_once()
        call_kwargs = mock_entity_class.call_args[1]
        assert call_kwargs["coordinator"] == mock_coordinator
        assert call_kwargs[CONF_NODE_ID] == TEST_NODE_ID
        assert call_kwargs["initial_data"] == {"value": 42}

        # async_add_entities should be called
        mock_add_entities.assert_called_once_with([mock_entity])

    async def test_handler_prevents_duplicate_entities(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler prevents duplicate entity creation."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        # Pre-populate with existing entity
        unique_id = f"{DOMAIN}_{TEST_NODE_ID}_test_entity"
        mock_coordinator.discovered_entities = {"test_entity": {unique_id: MagicMock()}}
        mock_add_entities = MagicMock()
        mock_coordinator.entity_callbacks = {"sensor": mock_add_entities}

        mock_entity_class = MagicMock(return_value=MagicMock())

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event
        hass.bus.async_fire(
            "esp_weaver_test_discovered",
            {CONF_NODE_ID: TEST_NODE_ID, "initial_data": {}},
        )
        await hass.async_block_till_done()

        # Entity should NOT be created (duplicate)
        mock_entity_class.assert_not_called()

    async def test_handler_no_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test handler handles missing coordinator gracefully."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None  # No coordinator

        mock_entity_class = MagicMock(return_value=MagicMock())

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_no_coord",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event
        hass.bus.async_fire(
            "esp_weaver_test_no_coord",
            {CONF_NODE_ID: TEST_NODE_ID, "initial_data": {}},
        )
        await hass.async_block_till_done()

        # Should not create entity (no coordinator)
        mock_entity_class.assert_not_called()

    async def test_handler_no_async_add_entities(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler handles missing async_add_entities callback."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        mock_coordinator.entity_callbacks = {}  # No callback

        mock_entity = MagicMock()
        mock_entity_class = MagicMock(return_value=mock_entity)

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_no_callback",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event
        hass.bus.async_fire(
            "esp_weaver_test_no_callback",
            {CONF_NODE_ID: TEST_NODE_ID, "initial_data": {}},
        )
        await hass.async_block_till_done()

        # Entity created but not added (no callback)
        mock_entity_class.assert_called_once()

    async def test_handler_with_extra_entity_kwargs(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler applies extra_entity_kwargs."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        mock_add_entities = MagicMock()
        mock_coordinator.entity_callbacks = {"sensor": mock_add_entities}

        mock_entity_class = MagicMock(return_value=MagicMock())

        def extra_kwargs_builder(data: dict) -> dict:
            return {"custom_param": data.get("custom_value", "default")}

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_extra_kwargs",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
            extra_entity_kwargs=extra_kwargs_builder,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event with custom value
        hass.bus.async_fire(
            "esp_weaver_test_extra_kwargs",
            {
                CONF_NODE_ID: TEST_NODE_ID,
                "initial_data": {},
                "custom_value": "my_value",
            },
        )
        await hass.async_block_till_done()

        # Verify extra kwargs were passed
        call_kwargs = mock_entity_class.call_args[1]
        assert call_kwargs["custom_param"] == "my_value"

    async def test_handler_exception_handling(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler catches and logs exceptions."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        mock_coordinator.entity_callbacks = {"sensor": MagicMock()}

        # Entity class that raises TypeError
        mock_entity_class = MagicMock(side_effect=TypeError("Missing argument"))

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_exception",
            entity_id_suffix="test_entity",
            entity_class=mock_entity_class,
        )

        create_discovery_listener(hass, TEST_NODE_ID, entry, config)

        # Fire event - should not raise
        hass.bus.async_fire(
            "esp_weaver_test_exception",
            {CONF_NODE_ID: TEST_NODE_ID, "initial_data": {}},
        )
        await hass.async_block_till_done()

        # Exception was caught (no crash)
        mock_entity_class.assert_called_once()


class TestSetupSingleEntityDiscoveryEventHandler:
    """Test setup_single_entity_discovery event handler execution."""

    async def test_handler_ignores_other_node_id(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler ignores events for other node IDs."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_add_entities = MagicMock()

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities={},
            async_add_entities=mock_add_entities,
        )

        factory_called = []

        def entity_factory(event_data, coordinator, node_id, device_name):
            factory_called.append(True)
            return MagicMock()

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_single_test",
            platform_name="test",
            entity_factory=entity_factory,
        )

        # Fire event for different node
        hass.bus.async_fire(
            "esp_weaver_single_test",
            {CONF_NODE_ID: "different_node", KEY_DEVICE_NAME: "Other Device"},
        )
        await hass.async_block_till_done()

        # Factory should NOT be called
        assert len(factory_called) == 0
        mock_add_entities.assert_not_called()

    async def test_handler_creates_and_tracks_entity(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler creates entity and tracks in discovered_entities."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_add_entities = MagicMock()
        discovered_entities: dict = {}

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities=discovered_entities,
            async_add_entities=mock_add_entities,
        )

        mock_entity = MagicMock()

        def entity_factory(event_data, coordinator, node_id, device_name):
            return mock_entity

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_single_create",
            platform_name="light",
            entity_factory=entity_factory,
        )

        # Fire event for correct node
        hass.bus.async_fire(
            "esp_weaver_single_create",
            {CONF_NODE_ID: TEST_NODE_ID, KEY_DEVICE_NAME: TEST_DEVICE_NAME},
        )
        await hass.async_block_till_done()

        # Entity should be tracked
        entity_key = f"{TEST_NODE_ID}_light"
        assert entity_key in discovered_entities
        assert discovered_entities[entity_key] == mock_entity

        # async_add_entities should be called
        mock_add_entities.assert_called_once_with([mock_entity])

    async def test_handler_prevents_duplicate_entities(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler prevents duplicate entity creation."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_add_entities = MagicMock()

        # Pre-populate with existing entity
        entity_key = f"{TEST_NODE_ID}_light"
        discovered_entities = {entity_key: MagicMock()}

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities=discovered_entities,
            async_add_entities=mock_add_entities,
        )

        factory_called = []

        def entity_factory(event_data, coordinator, node_id, device_name):
            factory_called.append(True)
            return MagicMock()

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_single_dup",
            platform_name="light",
            entity_factory=entity_factory,
        )

        # Fire event
        hass.bus.async_fire(
            "esp_weaver_single_dup",
            {CONF_NODE_ID: TEST_NODE_ID, KEY_DEVICE_NAME: TEST_DEVICE_NAME},
        )
        await hass.async_block_till_done()

        # Factory should NOT be called (duplicate)
        assert len(factory_called) == 0
        mock_add_entities.assert_not_called()

    async def test_handler_factory_returns_none(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler handles when factory returns None."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_add_entities = MagicMock()
        discovered_entities: dict = {}

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities=discovered_entities,
            async_add_entities=mock_add_entities,
        )

        def entity_factory(event_data, coordinator, node_id, device_name):
            return None  # Factory returns None

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_single_none",
            platform_name="light",
            entity_factory=entity_factory,
        )

        # Fire event
        hass.bus.async_fire(
            "esp_weaver_single_none",
            {CONF_NODE_ID: TEST_NODE_ID, KEY_DEVICE_NAME: TEST_DEVICE_NAME},
        )
        await hass.async_block_till_done()

        # Nothing should be added
        assert len(discovered_entities) == 0
        mock_add_entities.assert_not_called()

    async def test_handler_default_device_name(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler uses default device name from config entry."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_add_entities = MagicMock()

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities={},
            async_add_entities=mock_add_entities,
        )

        received_device_name = []

        def entity_factory(event_data, coordinator, node_id, device_name):
            received_device_name.append(device_name)
            return MagicMock()

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_single_default_name",
            platform_name="light",
            entity_factory=entity_factory,
        )

        # Fire event without device_name
        hass.bus.async_fire(
            "esp_weaver_single_default_name",
            {CONF_NODE_ID: TEST_NODE_ID},  # No KEY_DEVICE_NAME
        )
        await hass.async_block_till_done()

        # Should use config_entry.title as default name
        assert len(received_device_name) == 1
        assert received_device_name[0] == TEST_DEVICE_NAME

    async def test_handler_exception_handling(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handler catches and logs exceptions."""
        entry = create_mock_config_entry(mock_config_entry_data)
        mock_add_entities = MagicMock()

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities={},
            async_add_entities=mock_add_entities,
        )

        def entity_factory(event_data, coordinator, node_id, device_name):
            raise ValueError("Invalid event data")

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_single_error",
            platform_name="light",
            entity_factory=entity_factory,
        )

        # Fire event - should not raise
        hass.bus.async_fire(
            "esp_weaver_single_error",
            {CONF_NODE_ID: TEST_NODE_ID, KEY_DEVICE_NAME: TEST_DEVICE_NAME},
        )
        await hass.async_block_till_done()

        # Exception was caught (no crash)
        mock_add_entities.assert_not_called()
