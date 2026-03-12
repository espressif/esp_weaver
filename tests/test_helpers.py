# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver helpers module."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.esp_weaver.helpers.discovery import (
    PLATFORM_CACHE_KEYS,
    DiscoveryConfig,
    PlatformSetupResult,
    setup_platform_discovery,
    setup_single_entity_discovery,
)
from custom_components.esp_weaver.helpers.light_control import (
    setup_light_control_listener,
)
from custom_components.esp_weaver.helpers.sensor_alert import SensorAlertService
from custom_components.esp_weaver.helpers.utils import (
    get_number_device_class_and_unit,
    get_sensor_mapping,
)
from custom_components.esp_weaver.iot.specs.keys import CONF_NODE_ID, KEY_DEVICE_NAME

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestSensorMapping:
    """Test sensor mapping functions."""

    def test_get_sensor_mapping_returns_dict(self) -> None:
        """Test that get_sensor_mapping returns a dictionary."""
        mapping = get_sensor_mapping()
        assert isinstance(mapping, dict)

    def test_get_sensor_mapping_has_temperature(self) -> None:
        """Test that mapping includes temperature sensor."""
        mapping = get_sensor_mapping()
        assert "temperature" in mapping
        device_class, state_class, _unit = mapping["temperature"]
        assert device_class == SensorDeviceClass.TEMPERATURE
        assert state_class == SensorStateClass.MEASUREMENT

    def test_get_sensor_mapping_cached(self) -> None:
        """Test that mapping is cached (same object returned)."""
        mapping1 = get_sensor_mapping()
        mapping2 = get_sensor_mapping()
        assert mapping1 is mapping2


class TestNumberDeviceClassAndUnit:
    """Test number device class and unit functions."""

    def test_get_number_device_class_temperature(self) -> None:
        """Test getting device class for temperature."""
        device_class, unit = get_number_device_class_and_unit("temperature")
        # Temperature should have both device_class and unit defined
        assert device_class == NumberDeviceClass.TEMPERATURE
        assert unit == UnitOfTemperature.CELSIUS

    def test_get_number_device_class_unknown(self) -> None:
        """Test getting device class for unknown sensor type."""
        device_class, unit = get_number_device_class_and_unit("unknown_sensor_type")
        assert device_class is None
        assert unit is None


class TestDiscoveryConfig:
    """Test DiscoveryConfig dataclass."""

    def test_discovery_config_basic(self) -> None:
        """Test basic DiscoveryConfig creation."""
        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
        )

        assert config.discovered_event == "esp_weaver_test_discovered"
        assert config.entity_id_suffix == "test_entity"
        assert config.entity_name == "Test Entity"  # Auto-generated

    def test_discovery_config_custom_name(self) -> None:
        """Test DiscoveryConfig with custom name."""
        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
            entity_name="Custom Name",
        )

        assert config.entity_name == "Custom Name"

    def test_discovery_config_with_extra_kwargs(self) -> None:
        """Test DiscoveryConfig with extra kwargs builder."""

        def extra_builder(data: dict) -> dict:
            return {"extra_key": data.get("value", "default")}

        config = DiscoveryConfig(
            discovered_event="esp_weaver_test_discovered",
            entity_id_suffix="test_entity",
            entity_class=MagicMock,
            extra_entity_kwargs=extra_builder,
        )

        assert config.extra_entity_kwargs is not None
        assert config.extra_entity_kwargs({"value": "test"}) == {"extra_key": "test"}


class TestPlatformSetupResult:
    """Test PlatformSetupResult class."""

    def test_platform_setup_result_creation(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test creating PlatformSetupResult."""
        async_add_entities = MagicMock()
        discovered_entities: dict[str, Any] = {}

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


class TestSetupPlatformDiscovery:
    """Test setup_platform_discovery function."""

    async def test_setup_platform_discovery_success(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test successful platform discovery setup."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=async_add_entities,
            platform_name="sensor",
        )

        assert result is not None
        assert result.coordinator == mock_coordinator
        assert result.node_id == TEST_NODE_ID

    async def test_setup_platform_discovery_no_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test platform discovery setup fails without coordinator."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None

        async_add_entities = MagicMock()

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=async_add_entities,
            platform_name="sensor",
        )

        assert result is None

    async def test_setup_platform_discovery_no_node_id(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test platform discovery setup fails without node_id."""
        entry = create_mock_config_entry(data={})  # No node_id
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        result = setup_platform_discovery(
            config_entry=entry,
            async_add_entities=async_add_entities,
            platform_name="sensor",
        )

        assert result is None

    async def test_setup_platform_discovery_stores_callback(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that setup stores async_add_entities callback."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        setup_platform_discovery(
            config_entry=entry,
            async_add_entities=async_add_entities,
            platform_name="sensor",
        )

        assert mock_coordinator.entity_callbacks["sensor"] == async_add_entities


class TestSetupSingleEntityDiscovery:
    """Test setup_single_entity_discovery function."""

    async def test_setup_single_entity_discovery_registers_listener(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that single entity discovery registers event listener."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities={},
            async_add_entities=async_add_entities,
        )

        def entity_factory(event_data, coordinator, node_id, device_name):
            return MagicMock()

        # This should not raise
        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_test_discovered",
            platform_name="test",
            entity_factory=entity_factory,
        )

        # Verify listener was registered via async_on_unload
        entry.async_on_unload.assert_called()


class TestDiscoveryEventHandling:
    """Test discovery event handling."""

    async def test_discovery_filters_by_node_id(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that discovery filters events by node_id."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities={},
            async_add_entities=async_add_entities,
        )

        entity_created = []

        def entity_factory(event_data, coordinator, node_id, device_name):
            mock_entity = MagicMock()
            entity_created.append(mock_entity)
            return mock_entity

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_test_discovered",
            platform_name="test",
            entity_factory=entity_factory,
        )

        # Fire event with non-matching node_id - should be filtered out
        hass.bus.async_fire(
            "esp_weaver_test_discovered",
            {CONF_NODE_ID: "different_node", KEY_DEVICE_NAME: "Other Device"},
        )
        await hass.async_block_till_done()

        # Entity should NOT be created for non-matching node_id
        assert len(entity_created) == 0
        async_add_entities.assert_not_called()

        # Fire event with matching node_id - should create entity
        hass.bus.async_fire(
            "esp_weaver_test_discovered",
            {CONF_NODE_ID: TEST_NODE_ID, KEY_DEVICE_NAME: TEST_DEVICE_NAME},
        )
        await hass.async_block_till_done()

        # Entity should be created for matching node_id
        assert len(entity_created) == 1
        async_add_entities.assert_called_once()

    async def test_discovery_prevents_duplicates(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that discovery prevents duplicate entities."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        # Pre-populate discovered entities to simulate existing entity
        discovered_entities = {f"{TEST_NODE_ID}_test": MagicMock()}

        result = PlatformSetupResult(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            discovered_entities=discovered_entities,
            async_add_entities=async_add_entities,
        )

        entity_created = []

        def entity_factory(event_data, coordinator, node_id, device_name):
            mock_entity = MagicMock()
            entity_created.append(mock_entity)
            return mock_entity

        setup_single_entity_discovery(
            hass=hass,
            config_entry=entry,
            result=result,
            discovered_event="esp_weaver_test_discovered",
            platform_name="test",
            entity_factory=entity_factory,
        )

        # Fire discovery event for entity that already exists
        hass.bus.async_fire(
            "esp_weaver_test_discovered",
            {CONF_NODE_ID: TEST_NODE_ID, KEY_DEVICE_NAME: TEST_DEVICE_NAME},
        )
        await hass.async_block_till_done()

        # Entity should NOT be created for existing key (duplicate prevention)
        assert len(entity_created) == 0
        async_add_entities.assert_not_called()


class TestPlatformCacheKeys:
    """Test platform cache key mapping."""

    def test_sensor_cache_key(self) -> None:
        """Test sensor platform uses 'sensors' cache key."""
        assert PLATFORM_CACHE_KEYS["sensor"] == "sensors"

    def test_binary_sensor_cache_key(self) -> None:
        """Test binary_sensor platform uses 'binary_sensors' cache key."""
        assert PLATFORM_CACHE_KEYS["binary_sensor"] == "binary_sensors"

    def test_light_cache_key(self) -> None:
        """Test light platform uses 'lights' cache key."""
        assert PLATFORM_CACHE_KEYS["light"] == "lights"

    def test_number_cache_key(self) -> None:
        """Test number platform uses 'numbers' cache key."""
        assert PLATFORM_CACHE_KEYS["number"] == "numbers"


# =============================================================================
# Light Control Listener
# =============================================================================
class TestLightControlListener:
    """Test light control listener functionality."""

    def test_setup_registers_listener(self) -> None:
        """Test listener is registered on bus."""
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_hass.bus.async_listen = MagicMock()
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(return_value=True)

        setup_light_control_listener(mock_hass, "node123", mock_api)
        mock_hass.bus.async_listen.assert_called_once()

    async def test_handler_filters_by_node_id(self) -> None:
        """Test handler filters events by node_id."""
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(return_value=True)

        setup_light_control_listener(mock_hass, "node123", mock_api)
        handler = mock_hass.bus.async_listen.call_args[0][1]

        mock_event = MagicMock()
        mock_event.data = {"node_id": "other_node", "properties": {"power": True}}
        await handler(mock_event)

        mock_api.set_local_ctrl_property.assert_not_called()

    async def test_handler_processes_matching_node(self) -> None:
        """Test handler processes events for matching node."""
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(return_value=True)

        setup_light_control_listener(mock_hass, "node123", mock_api)
        handler = mock_hass.bus.async_listen.call_args[0][1]

        mock_event = MagicMock()
        mock_event.data = {"node_id": "node123", "properties": {"power": True}}
        await handler(mock_event)

        mock_api.set_local_ctrl_property.assert_called()

    async def test_handler_ignores_empty_properties(self) -> None:
        """Test handler ignores events with empty properties."""
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(return_value=True)

        setup_light_control_listener(mock_hass, "node123", mock_api)
        handler = mock_hass.bus.async_listen.call_args[0][1]

        mock_event = MagicMock()
        mock_event.data = {"node_id": "node123", "properties": {}}
        await handler(mock_event)

        mock_api.set_local_ctrl_property.assert_not_called()

    async def test_handler_handles_network_errors(self) -> None:
        """Test handler handles network errors gracefully."""
        mock_hass = MagicMock()
        mock_hass.bus = MagicMock()
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(
            side_effect=OSError("Network error")
        )

        setup_light_control_listener(mock_hass, "node123", mock_api)
        handler = mock_hass.bus.async_listen.call_args[0][1]

        mock_event = MagicMock()
        mock_event.data = {"node_id": "node123", "properties": {"brightness": 128}}

        # Should not raise
        await handler(mock_event)


# =============================================================================
# Sensor Alert Service
# =============================================================================


def _create_mock_entity_registry(
    min_entity_id: str | None = "number.min_threshold",
    max_entity_id: str | None = "number.max_threshold",
) -> MagicMock:
    """Create a mock entity registry with async_get_entity_id support."""
    mock_registry = MagicMock()

    def get_entity_id(domain: str, platform: str, unique_id: str) -> str | None:
        if "min_threshold" in unique_id:
            return min_entity_id
        if "max_threshold" in unique_id:
            return max_entity_id
        return None

    mock_registry.async_get_entity_id = MagicMock(side_effect=get_entity_id)
    return mock_registry


class TestSensorAlertServiceThresholds:
    """Test SensorAlertService threshold functionality."""

    def test_get_valid_thresholds(self) -> None:
        """Test getting valid threshold values."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "15.0"
        max_state = MagicMock()
        max_state.state = "30.0"

        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state
            if "min" in entity_id
            else max_state
            if "max" in entity_id
            else None
        )

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            result = service._get_threshold_values("node123", "temperature")

            assert result is not None
            assert result[0] == 15.0
            assert result[1] == 30.0

    def test_get_missing_threshold_returns_none(self) -> None:
        """Test missing threshold entity returns None."""
        mock_hass = MagicMock()
        mock_hass.states.get.return_value = None

        mock_registry = _create_mock_entity_registry(
            min_entity_id=None, max_entity_id=None
        )
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            result = service._get_threshold_values("node123", "temperature")

            assert result is None

    def test_get_invalid_threshold_value(self) -> None:
        """Test invalid threshold value returns None."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "invalid"
        max_state = MagicMock()
        max_state.state = "30.0"

        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            result = service._get_threshold_values("node123", "temperature")

            assert result is None

    def test_get_threshold_state_returns_none(self) -> None:
        """Test threshold returns None when state entity returns None."""
        mock_hass = MagicMock()
        # Return None for states.get() to test line 199-200
        mock_hass.states.get.return_value = None

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            result = service._get_threshold_values("node123", "temperature")

            assert result is None

    def test_get_threshold_min_state_none_only(self) -> None:
        """Test threshold returns None when only min state is None."""
        mock_hass = MagicMock()
        max_state = MagicMock()
        max_state.state = "30.0"
        # Return None for min, valid for max
        mock_hass.states.get.side_effect = lambda entity_id: (
            None if "min" in entity_id else max_state
        )

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            result = service._get_threshold_values("node123", "temperature")

            assert result is None


class TestSensorAlertServiceViolations:
    """Test SensorAlertService violation handling."""

    async def test_no_thresholds_configured_no_notification(self) -> None:
        """Test no notification when no thresholds configured."""
        mock_hass = MagicMock()
        mock_hass.states.get.return_value = None
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()
        mock_hass.data = {}

        mock_registry = _create_mock_entity_registry(
            min_entity_id=None, max_entity_id=None
        )
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 25.0, None
            )

            mock_hass.services.async_call.assert_not_called()

    async def test_value_within_thresholds_no_notification(self) -> None:
        """Test no notification when value within thresholds."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "15.0"
        max_state = MagicMock()
        max_state.state = "30.0"
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()
        mock_hass.data = {}

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 25.0, None
            )

            mock_hass.services.async_call.assert_not_called()

    async def test_high_threshold_violation_creates_notification(self) -> None:
        """Test notification created for high threshold violation."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "15.0"
        max_state = MagicMock()
        max_state.state = "30.0"
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock()
        mock_hass.data = {"esp_weaver": {"api": mock_api}}

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 35.0, None
            )

            mock_hass.services.async_call.assert_called()


class TestSensorAlertServiceNotifications:
    """Test SensorAlertService notification management."""

    async def test_create_notification(self) -> None:
        """Test creating notification."""
        mock_hass = MagicMock()
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        service = SensorAlertService(mock_hass, "esp_weaver")
        await service._create_notification(
            node_id="node123",
            device_name="My Sensor",
            sensor_type="temperature",
            alert_type="high",
            current_value=35.0,
            threshold_value=30.0,
            unit="°C",
        )

        mock_hass.services.async_call.assert_called_once()
        call_args = mock_hass.services.async_call.call_args[0]
        assert call_args[0] == "persistent_notification"
        assert call_args[1] == "create"

    async def test_clear_notifications(self) -> None:
        """Test clearing notifications."""
        mock_hass = MagicMock()
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        service = SensorAlertService(mock_hass, "esp_weaver")
        await service._clear_notifications("node123", "temperature")

        # Should be called twice (for high and low)
        assert mock_hass.services.async_call.call_count == 2


class TestSensorAlertServiceImperialUnits:
    """Test SensorAlertService with imperial unit conversions."""

    async def test_high_violation_with_imperial_temperature(self) -> None:
        """Test high threshold violation converts to Fahrenheit for imperial users."""
        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        min_state = MagicMock()
        min_state.state = "59.0"  # 15°C in °F
        max_state = MagicMock()
        max_state.state = "86.0"  # 30°C in °F
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            # Value in native units (Celsius) - 35°C exceeds max threshold
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 35.0, None
            )

            # Notification should have been created
            mock_hass.services.async_call.assert_called()

    async def test_low_violation_with_imperial_pressure(self) -> None:
        """Test low threshold violation converts to inHg for imperial users."""
        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        min_state = MagicMock()
        min_state.state = "29.5"  # ~999 hPa in inHg
        max_state = MagicMock()
        max_state.state = "30.5"  # ~1033 hPa in inHg
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            # Value in native units (hPa) - 950 hPa is below min threshold
            await service.check_and_handle_violations(
                "node123", "My Sensor", "pressure", 950.0, None
            )

            mock_hass.services.async_call.assert_called()

    async def test_high_violation_with_imperial_illuminance(self) -> None:
        """Test high threshold violation uses lx for imperial users.

        HA does not auto-convert lx to fc, so illuminance always uses lx
        for consistency between sensor UI, threshold UI, and notifications.
        """
        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        min_state = MagicMock()
        min_state.state = "100"  # 100 lx (native unit, no fc conversion)
        max_state = MagicMock()
        max_state.state = "1000"  # 1000 lx (native unit, no fc conversion)

        def get_state(entity_id: str) -> MagicMock | None:
            if "min_threshold" in entity_id:
                return min_state
            if "max_threshold" in entity_id:
                return max_state
            return None

        mock_hass.states.get.side_effect = get_state
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")
            # Value in native units (lux) - 2000 lx exceeds max threshold of 1000 lx
            await service.check_and_handle_violations(
                "node123", "My Sensor", "illuminance", 2000.0, None
            )

            mock_hass.services.async_call.assert_called()

    async def test_violation_clears_when_back_in_range(self) -> None:
        """Test violation is cleared when value returns to normal range."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "15.0"
        max_state = MagicMock()
        max_state.state = "30.0"
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")

            # First: trigger a violation
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 35.0, None
            )
            # Violation should be tracked
            assert "node123_temperature" in service._active_violations

            # Reset mock to check next call
            mock_hass.services.async_call.reset_mock()

            # Then: return to normal range
            await service.check_and_handle_violations(
                "node123",
                "My Sensor",
                "temperature",
                25.0,
                35.0,  # old_value was violated
            )

            # Clear notifications should have been called
            mock_hass.services.async_call.assert_called()
            # Violation should be cleared
            assert "node123_temperature" not in service._active_violations

    async def test_same_violation_type_does_not_resend(self) -> None:
        """Test that same ongoing violation doesn't resend notifications."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "15.0"
        max_state = MagicMock()
        max_state.state = "30.0"
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")

            # First violation
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 35.0, None
            )
            call_count_after_first = mock_hass.services.async_call.call_count

            # Same type of violation continues
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 36.0, None
            )
            call_count_after_second = mock_hass.services.async_call.call_count

            # Should not have added more calls (same violation type)
            assert call_count_after_second == call_count_after_first

    async def test_violation_type_changes_dismisses_previous(self) -> None:
        """Test that changing violation type (high->low) dismisses previous."""
        mock_hass = MagicMock()
        min_state = MagicMock()
        min_state.state = "15.0"
        max_state = MagicMock()
        max_state.state = "30.0"
        mock_hass.states.get.side_effect = lambda entity_id: (
            min_state if "min" in entity_id else max_state
        )
        mock_hass.services = MagicMock()
        mock_hass.services.async_call = AsyncMock()

        mock_registry = _create_mock_entity_registry()
        with patch(
            "custom_components.esp_weaver.helpers.sensor_alert.er.async_get",
            return_value=mock_registry,
        ):
            service = SensorAlertService(mock_hass, "esp_weaver")

            # First: trigger a HIGH violation (value above max)
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 35.0, None
            )
            # Violation should be tracked as "high"
            assert service._active_violations.get("node123_temperature") == "high"

            # Reset mock
            mock_hass.services.async_call.reset_mock()

            # Then: trigger a LOW violation (value below min)
            await service.check_and_handle_violations(
                "node123", "My Sensor", "temperature", 10.0, None
            )

            # Violation type should have changed to "low"
            assert service._active_violations.get("node123_temperature") == "low"
            # Should have called services (dismiss old + create new)
            assert mock_hass.services.async_call.call_count >= 2
