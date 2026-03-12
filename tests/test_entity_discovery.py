# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the entity_discovery module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.managers.entity_discovery import (
    CONTROLLER_PLATFORMS,
    DeviceDiscoveryManager,
    DiscoveryContext,
)


class TestDiscoveryContext:
    """Test DiscoveryContext dataclass."""

    def test_creation(self) -> None:
        """Test creating discovery context."""
        mock_hass = MagicMock()

        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={"Light": {"Power": True}},
        )

        assert ctx.hass == mock_hass
        assert ctx.domain == "esp_weaver"
        assert ctx.node_id == "node123"
        assert ctx.device_info == {"name": "Test Device"}
        assert ctx.current_values == {"Light": {"Power": True}}


class TestControllerPlatforms:
    """Test CONTROLLER_PLATFORMS constant."""

    def test_contains_expected_platforms(self) -> None:
        """Test CONTROLLER_PLATFORMS contains expected platforms."""
        assert "imu_gesture" in CONTROLLER_PLATFORMS
        assert "interactive_input" in CONTROLLER_PLATFORMS
        assert "battery_energy" in CONTROLLER_PLATFORMS
        assert "low_power_sleep" in CONTROLLER_PLATFORMS

    def test_is_frozen_set(self) -> None:
        """Test CONTROLLER_PLATFORMS is a frozen set."""
        assert isinstance(CONTROLLER_PLATFORMS, frozenset)


class TestDeviceDiscoveryManagerInit:
    """Test DeviceDiscoveryManager initialization."""

    def test_init(self, mock_hass: MagicMock, mock_registry: MagicMock) -> None:
        """Test basic initialization."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        assert manager.hass == mock_hass
        assert manager.domain == "esp_weaver"
        assert manager.registry == mock_registry

    def test_discovery_handlers(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test discovery handlers are registered."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        assert "light" in manager._discovery_handlers
        assert "binary_sensor" in manager._discovery_handlers
        assert "sensor" in manager._discovery_handlers


class TestDeviceDiscoveryManagerFireEvent:
    """Test DeviceDiscoveryManager._fire_event method."""

    def test_fire_event(self, mock_hass: MagicMock, mock_registry: MagicMock) -> None:
        """Test firing discovery event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        manager._fire_event("test_event", {"key": "value"})

        mock_hass.bus.async_fire.assert_called_once_with("test_event", {"key": "value"})


class TestDeviceDiscoveryManagerValueExtraction:
    """Test DeviceDiscoveryManager value extraction methods."""

    def test_extract_binary_sensor_state_from_device_type(self) -> None:
        """Test extracting binary sensor state from device type."""
        current_values = {"Binary Sensor": {"State": True}}
        initial = {"state": False}

        result = DeviceDiscoveryManager._extract_binary_sensor_state(
            current_values, initial
        )

        assert result is True

    def test_extract_binary_sensor_state_bool(self) -> None:
        """Test extracting binary sensor state when value is bool."""
        current_values = {"Binary Sensor": False}
        initial = {"state": True}

        result = DeviceDiscoveryManager._extract_binary_sensor_state(
            current_values, initial
        )

        assert result is False

    def test_extract_binary_sensor_state_default(self) -> None:
        """Test using initial value when current values are empty."""
        current_values = {}
        initial = {"state": True}

        result = DeviceDiscoveryManager._extract_binary_sensor_state(
            current_values, initial
        )

        assert result is True

    def test_extract_sensor_value(self) -> None:
        """Test extracting sensor value."""
        current_values = {"Temperature Sensor": {"temperature": 25.5, "humidity": 60}}
        entity_info = {
            "device_type": "Temperature Sensor",
            "param": {"name": "temperature"},
        }

        result = DeviceDiscoveryManager._extract_sensor_value(
            current_values, entity_info
        )

        assert result == 25.5

    def test_extract_sensor_value_no_current_values(self) -> None:
        """Test extracting sensor value when no current values."""
        result = DeviceDiscoveryManager._extract_sensor_value(None, {})

        assert result is None

    def test_extract_threshold_values(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test extracting threshold values."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        # Use correct keys: device_param_prefix for temperature is "temp"
        current_values = {
            "Temperature Sensor": {
                "temp_min_threshold": 15.0,
                "temp_max_threshold": 30.0,
            }
        }

        result = manager._extract_threshold_values(current_values, "temperature")

        assert isinstance(result, dict)
        assert result.get("min") == 15.0
        assert result.get("max") == 30.0

    def test_extract_threshold_values_empty(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test extracting threshold values when empty."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._extract_threshold_values(None, "temperature")

        assert result == {}


class TestDeviceDiscoveryManagerFireLightDiscovered:
    """Test DeviceDiscoveryManager._fire_light_discovered method."""

    def test_fire_light_discovered(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing light discovered event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={"Light": {"Power": True, "Brightness": 100}},
        )
        entity_info = {
            "device_name": "Test Light",
            "capabilities": {"power": True, "brightness": True},
        }

        manager._fire_light_discovered(ctx, entity_info)

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert call_args[0][0].endswith("_light_discovered")
        event_data = call_args[0][1]
        assert event_data["node_id"] == "node123"
        assert event_data["device_name"] == "Test Light"


class TestDeviceDiscoveryManagerFireBinarySensorDiscovered:
    """Test DeviceDiscoveryManager._fire_binary_sensor_discovered method."""

    def test_fire_binary_sensor_discovered(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing binary sensor discovered event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={},
        )
        entity_info = {
            "device_name": "Motion Sensor",
            "config": {"device_class": "motion"},
            "initial_values": {"state": False, "debounce_time": 100},
        }

        manager._fire_binary_sensor_discovered(ctx, entity_info)

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        event_data = call_args[0][1]
        assert event_data["node_id"] == "node123"
        assert event_data["params"]["device_class"] == "motion"


class TestDeviceDiscoveryManagerFireSensorDiscovered:
    """Test DeviceDiscoveryManager._fire_sensor_discovered method."""

    def test_fire_sensor_discovered_with_param(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing sensor discovered event with param."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={},
        )
        entity_info = {
            "device_name": "Temperature Sensor",
            "sensor_type": "temperature",
            "sensor_name": "Temperature",
            "param": {"name": "temperature"},
        }

        manager._fire_sensor_discovered(ctx, entity_info)

        mock_hass.bus.async_fire.assert_called_once()

    def test_fire_sensor_discovered_without_param(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test sensor discovered event not fired without param."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={},
            current_values={},
        )
        entity_info = {"device_name": "Sensor"}  # No param

        manager._fire_sensor_discovered(ctx, entity_info)

        mock_hass.bus.async_fire.assert_not_called()


class TestDeviceDiscoveryManagerTriggerPlatformDiscovery:
    """Test DeviceDiscoveryManager.trigger_platform_discovery method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with platform discovery support."""
        registry = MagicMock()
        registry.add_discovered_platform.return_value = True
        return registry

    def test_trigger_platform_discovery(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test triggering platform discovery fires correct event with payload."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        device_config = {
            "platforms": {
                "light": [{"device_name": "Test Light", "initial_values": {}}],
            }
        }
        device_info = {"name": "Test Device"}

        manager.trigger_platform_discovery("node123", device_config, {}, device_info)

        # Verify async_fire was called
        assert mock_hass.bus.async_fire.called

        # Verify the event name and payload structure
        call_args = mock_hass.bus.async_fire.call_args
        event_name = call_args[0][0]
        event_data = call_args[0][1]

        # Event should be the light_discovered event
        assert event_name.endswith("_light_discovered")

        # Verify expected payload structure
        assert "node_id" in event_data
        assert event_data["node_id"] == "node123"
        assert "device_name" in event_data
        assert event_data["device_name"] == "Test Light"
        assert "device_info" in event_data
        assert event_data["device_info"] == device_info
        # Light discovery event includes light_data (converted from esp_light_data)
        assert "light_data" in event_data


class TestDeviceDiscoveryManagerParseAndDiscover:
    """Test DeviceDiscoveryManager.parse_and_discover_entities method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with device and platform support."""
        registry = MagicMock()
        registry.get_device.return_value = MagicMock()
        registry.get_config_entry_id.return_value = None
        registry.add_discovered_platform.return_value = True
        return registry

    @pytest.mark.asyncio
    async def test_parse_and_discover_no_properties(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test with no properties."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        await manager.parse_and_discover_entities("node123", None)

        # Should not fire any events
        mock_hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_and_discover_empty_properties(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test with empty properties list."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        await manager.parse_and_discover_entities("node123", [])

        mock_hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_and_discover_with_config(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test with valid config property."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        config_data = {
            "node_id": "node123",
            "info": {"name": "Test Device", "fw_version": "1.0.0"},
            "devices": [
                {
                    "name": "Light",
                    "type": "esp.device.lightbulb",
                    "params": [{"name": "power", "properties": ["read", "write"]}],
                }
            ],
        }
        properties = [
            {
                "name": "config",
                "value": json.dumps(config_data).encode(),
            }
        ]

        await manager.parse_and_discover_entities("node123", properties)

        # Verify device registry was queried
        mock_registry.get_device.assert_called()

        # Verify device info was updated (if device exists)
        # The manager should attempt to get and update device info
        call_args = mock_registry.get_device.call_args
        assert call_args is not None
        assert call_args[0][0] == "node123"  # node_id passed correctly


class TestDeviceDiscoveryManagerConfigParsing:
    """Test DeviceDiscoveryManager config parsing helpers."""

    def test_find_config_property_by_name(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test finding config property by name."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        properties = [
            {"name": "params", "value": b"{}"},
            {"name": "config", "value": b'{"node_id": "test"}'},
        ]

        result = manager._find_config_property(properties)

        assert result is not None
        assert result["name"] == "config"

    def test_find_config_property_not_found(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test when config property not found."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        properties = [{"name": "params", "value": b"{}"}]

        result = manager._find_config_property(properties)

        assert result is None

    def test_find_config_property_empty_list(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test with empty properties list."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._find_config_property([])

        assert result is None

    def test_find_config_property_none(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test with None properties."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._find_config_property(None)

        assert result is None


class TestDeviceDiscoveryManagerControllerDiscovery:
    """Test DeviceDiscoveryManager controller discovery methods."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with platform discovery support."""
        registry = MagicMock()
        registry.add_discovered_platform.return_value = True
        return registry

    def test_fire_controller_discovered_battery_energy(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing battery_energy controller event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={"Battery Energy": {"level": 85, "charging": True}},
        )
        entity_info = {
            "device_name": "Battery Monitor",
            "entity_name": "battery_controller",
            "capabilities": {},
            "params": [],
        }

        manager._fire_controller_discovered(ctx, "battery_energy", entity_info)

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert "battery_energy" in call_args[0][0]
        event_data = call_args[0][1]
        assert event_data["node_id"] == "node123"

    def test_fire_controller_discovered_imu_gesture(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing imu_gesture controller event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={"IMU Gesture": {"gesture": "tap"}},
        )
        entity_info = {
            "device_name": "Gesture Sensor",
            "entity_name": "gesture_controller",
            "capabilities": {},
            "params": [],
        }

        manager._fire_controller_discovered(ctx, "imu_gesture", entity_info)

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert "imu_gesture" in call_args[0][0]

    def test_fire_controller_discovered_interactive_input(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing interactive_input controller event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={"Interactive Input": {"button": "pressed"}},
        )
        entity_info = {
            "device_name": "Button Input",
            "entity_name": "input_controller",
            "capabilities": {},
            "params": [],
        }

        manager._fire_controller_discovered(ctx, "interactive_input", entity_info)

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert "interactive_input" in call_args[0][0]

    def test_fire_controller_discovered_low_power_sleep(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test firing low_power_sleep controller event."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={"name": "Test Device"},
            current_values={"Low Power Sleep": {"wake_count": 5}},
        )
        entity_info = {
            "device_name": "Sleep Monitor",
            "entity_name": "sleep_controller",
            "capabilities": {},
            "params": [],
        }

        manager._fire_controller_discovered(ctx, "low_power_sleep", entity_info)

        mock_hass.bus.async_fire.assert_called_once()
        call_args = mock_hass.bus.async_fire.call_args
        assert "low_power_sleep" in call_args[0][0]

    def test_fire_controller_discovered_unknown_type(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test unknown controller type logs warning."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={},
            current_values={},
        )
        entity_info = {"device_name": "Unknown", "params": []}

        # Should not raise, just log warning
        manager._fire_controller_discovered(ctx, "unknown_type", entity_info)

        # Should not fire any event
        mock_hass.bus.async_fire.assert_not_called()


class TestDeviceDiscoveryManagerConvertControllerData:
    """Test DeviceDiscoveryManager._convert_controller_data method."""

    def test_convert_battery_energy_data(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test battery energy data conversion."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        # Use ESP property names as they appear in device data
        current_values = {
            "Battery & Energy": {
                "Battery Level": 85,
                "Charging Status": True,
                "Voltage": 3.7,
            }
        }

        result = manager._convert_controller_data("battery_energy", current_values)

        assert isinstance(result, dict)
        # Verify expected keys from build_battery_event_payload
        assert "battery_level" in result
        assert result["battery_level"] == 85
        assert "charging_status" in result
        assert result["charging_status"] is True
        assert "voltage" in result
        assert result["voltage"] == 3.7

    def test_convert_imu_gesture_data(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test IMU gesture data conversion."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        # Use ESP property names as they appear in device data
        current_values = {
            "IMU Gesture Sensor": {
                "Gesture Type": "tap",
                "Gesture Confidence": 0.95,
                "X Orientation": 10,
            }
        }

        result = manager._convert_controller_data("imu_gesture", current_values)

        assert isinstance(result, dict)
        # Verify expected keys from build_gesture_event_payload
        assert "gesture_type" in result
        assert result["gesture_type"] == "tap"
        assert "gesture_confidence" in result
        assert isinstance(result["gesture_confidence"], float)
        assert result["gesture_confidence"] == 0.95
        assert "x_orientation" in result
        assert result["x_orientation"] == 10

    def test_convert_interactive_input_data(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test interactive input data conversion."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        # Use ESP property names as they appear in device data
        current_values = {
            "Interactive Input": {
                "Input Type": "button",
                "Last Event": "single_press",
                "Input Value": 1,
            }
        }

        result = manager._convert_controller_data("interactive_input", current_values)

        assert isinstance(result, dict)
        # Verify expected keys from build_input_event_payload
        assert "input_type" in result
        assert result["input_type"] == "button"
        assert "last_event" in result
        assert result["last_event"] == "single_press"
        assert "input_value" in result
        assert result["input_value"] == 1

    def test_convert_low_power_sleep_data(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test low power sleep data conversion."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        # Use ESP property names as they appear in device data
        current_values = {
            "Low Power & Sleep": {
                "Wake Count": 5,
                "Sleep Duration": 3600,
                "Sleep State": "awake",
                "Wake Reason": "timer",
            }
        }

        result = manager._convert_controller_data("low_power_sleep", current_values)

        assert isinstance(result, dict)
        # Verify expected keys from build_sleep_event_payload
        assert "wake_count" in result
        assert result["wake_count"] == 5
        assert "sleep_duration" in result
        assert result["sleep_duration"] == 3600
        assert "sleep_state" in result
        assert result["sleep_state"] == "awake"
        assert "wake_reason" in result
        assert result["wake_reason"] == "timer"

    def test_convert_unknown_platform(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test unknown platform returns empty dict."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._convert_controller_data("unknown_platform", {})

        assert result == {}

    def test_convert_empty_current_values(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test empty current values returns empty dict."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._convert_controller_data("battery_energy", {})

        assert result == {}

    def test_convert_none_current_values(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test None current values returns empty dict."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._convert_controller_data("battery_energy", None)

        assert result == {}

    def test_convert_missing_device_type_data(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test missing device type data returns empty dict."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        # Has data but not for Battery Energy
        current_values = {"Light": {"power": True}}

        result = manager._convert_controller_data("battery_energy", current_values)

        assert result == {}


class TestDeviceDiscoveryManagerTriggerEntityDiscovery:
    """Test DeviceDiscoveryManager._trigger_entity_discovery method."""

    def test_trigger_entity_discovery_light(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test triggering light entity discovery."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={},
            current_values={},
        )
        entity_info = {"device_name": "Test Light", "capabilities": {}}

        manager._trigger_entity_discovery(ctx, "light", entity_info)

        mock_hass.bus.async_fire.assert_called_once()

    def test_trigger_entity_discovery_controller_platform(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test triggering controller platform discovery."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        ctx = DiscoveryContext(
            hass=mock_hass,
            domain="esp_weaver",
            node_id="node123",
            device_info={},
            current_values={},
        )
        entity_info = {"device_name": "Battery", "params": []}

        manager._trigger_entity_discovery(ctx, "battery_energy", entity_info)

        mock_hass.bus.async_fire.assert_called_once()


class TestDeviceDiscoveryManagerParseAndDiscoverEdgeCases:
    """Test parse_and_discover_entities edge cases."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry returning None for device lookup."""
        registry = MagicMock()
        registry.get_device.return_value = None
        registry.get_config_entry_id.return_value = None
        return registry

    @pytest.mark.asyncio
    async def test_parse_and_discover_json_decode_error(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test handling of JSONDecodeError."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        properties = [{"name": "config", "value": b"invalid json"}]

        # Should not raise
        await manager.parse_and_discover_entities("node123", properties)

        # Should not fire any events due to error
        mock_hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_and_discover_no_config_property(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test handling when no config property found."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        properties = [{"name": "other", "value": b"{}"}]

        await manager.parse_and_discover_entities("node123", properties)

        mock_hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_and_discover_with_preferred_device_name(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test using preferred device name."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        config_data = {
            "node_id": "node123",
            "info": {"name": "Original Name"},
            "devices": [],
        }
        properties = [{"name": "config", "value": json.dumps(config_data).encode()}]

        with patch.object(
            manager._device_parser, "parse_device_config", return_value={}
        ) as mock_parse:
            await manager.parse_and_discover_entities(
                "node123", properties, preferred_device_name="Custom Name"
            )

            # Verify preferred name was passed to parser
            mock_parse.assert_called_once()
            call_args = mock_parse.call_args
            # Check that "Custom Name" was passed as the second positional argument
            assert call_args.args[1] == "Custom Name"

    @pytest.mark.asyncio
    async def test_parse_and_discover_updates_device_registry(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test device registry is updated with parsed config."""
        mock_device = MagicMock()
        mock_registry.get_device.return_value = mock_device
        mock_registry.add_discovered_platform.return_value = True

        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        config_data = {
            "node_id": "node123",
            "info": {"name": "Test Device", "fw_version": "1.0"},
            "devices": [],
        }
        properties = [{"name": "config", "value": json.dumps(config_data).encode()}]

        await manager.parse_and_discover_entities("node123", properties)

        # Device should be updated
        mock_registry.get_device.assert_called_with("node123")


class TestDeviceDiscoveryManagerGetPreferredDeviceName:
    """Test DeviceDiscoveryManager._get_preferred_device_name method."""

    def test_get_preferred_name_from_config_entry(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test getting device name from config entry."""
        mock_entry = MagicMock()
        mock_entry.title = "My Custom Device"
        mock_hass.config_entries.async_get_entry.return_value = mock_entry
        mock_registry.get_config_entry_id.return_value = "entry_123"

        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._get_preferred_device_name("node123")

        assert result == "My Custom Device"

    def test_get_preferred_name_no_entry_id(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test getting device name when no entry ID."""
        mock_registry.get_config_entry_id.return_value = None

        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._get_preferred_device_name("node123")

        assert result is None

    def test_get_preferred_name_no_config_entry(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test getting device name when config entry not found."""
        mock_hass.config_entries.async_get_entry.return_value = None
        mock_registry.get_config_entry_id.return_value = "entry_123"

        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._get_preferred_device_name("node123")

        assert result is None


class TestDeviceDiscoveryManagerParseDeviceConfig:
    """Test DeviceDiscoveryManager._parse_device_config method."""

    def test_parse_device_config_none(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test parsing None config property."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)

        result = manager._parse_device_config(None, "Device Name")

        assert result is None

    def test_parse_device_config_dict_value(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test parsing dict config value."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        config_property = {
            "name": "config",
            "value": {"node_id": "node123", "info": {}, "devices": []},
        }

        result = manager._parse_device_config(config_property, "Device Name")

        # Dict values are supported and parsed directly
        assert result is not None
        assert isinstance(result, dict)
        assert "device_info" in result

    def test_parse_device_config_bytes_value(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test parsing bytes config value."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        config_data = {"node_id": "node123", "info": {}, "devices": []}
        config_property = {"name": "config", "value": json.dumps(config_data).encode()}

        result = manager._parse_device_config(config_property, "Device Name")

        # Bytes value should be successfully parsed into structured result
        assert result is not None
        assert isinstance(result, dict)
        # Result contains device_info, platforms, entities - not raw config fields
        assert "device_info" in result

    def test_parse_device_config_no_value(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test parsing config with no value."""
        manager = DeviceDiscoveryManager(mock_hass, "esp_weaver", mock_registry)
        config_property = {"name": "config", "value": None}

        result = manager._parse_device_config(config_property, "Device Name")

        assert result is None


class TestDeviceDiscoveryManagerExtractSensorValueEdgeCases:
    """Test _extract_sensor_value edge cases."""

    def test_extract_sensor_value_missing_device_type(self) -> None:
        """Test extracting sensor value when device type not in current values."""
        current_values = {"Other Device": {"temperature": 25}}
        entity_info = {
            "device_type": "Temperature Sensor",
            "param": {"name": "temperature"},
        }

        result = DeviceDiscoveryManager._extract_sensor_value(
            current_values, entity_info
        )

        assert result is None

    def test_extract_sensor_value_no_param_name(self) -> None:
        """Test extracting sensor value when no param name."""
        current_values = {"Temperature Sensor": {"temperature": 25}}
        entity_info = {"device_type": "Temperature Sensor", "param": {}}

        result = DeviceDiscoveryManager._extract_sensor_value(
            current_values, entity_info
        )

        assert result is None

    def test_extract_sensor_value_non_dict_data(self) -> None:
        """Test extracting sensor value when sensor data is not a dict."""
        current_values = {"Temperature Sensor": "not a dict"}
        entity_info = {
            "device_type": "Temperature Sensor",
            "param": {"name": "temperature"},
        }

        result = DeviceDiscoveryManager._extract_sensor_value(
            current_values, entity_info
        )

        assert result is None
