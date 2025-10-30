# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver iot utility functions."""

from unittest.mock import MagicMock

from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest

from custom_components.esp_weaver.iot.specs.battery_specs import (
    ICON_BATTERY_10,
    ICON_BATTERY_30,
    ICON_BATTERY_ALERT,
    ICON_BATTERY_CHARGING,
    ICON_BATTERY_FULL,
    ICON_BATTERY_LOW,
)
from custom_components.esp_weaver.iot.specs.gesture_specs import DEFAULT_GESTURE_ICON
from custom_components.esp_weaver.iot.specs.keys import (
    KEY_BATTERY_LEVEL,
    KEY_GESTURE_CONFIDENCE,
    KEY_GESTURE_DISPLAY_DURATION,
    KEY_GESTURE_TYPE,
    KEY_ORIENTATION_CHANGE,
    KEY_ORIENTATION_CHANGE_SHORT,
    KEY_SENSITIVITY,
    KEY_TEMPERATURE,
    KEY_VOLTAGE,
)
from custom_components.esp_weaver.iot.utils.battery_utils import (
    BatteryProcessor,
    build_battery_notification_data,
    get_battery_icon,
    get_battery_notification_ids_to_clear,
    parse_battery_update,
)
from custom_components.esp_weaver.iot.utils.binary_sensor_utils import (
    get_binary_sensor_device_class,
    process_binary_sensor_update,
)
from custom_components.esp_weaver.iot.utils.gesture_utils import (
    GestureProcessor,
    get_gesture_icon,
)
from custom_components.esp_weaver.iot.utils.input_utils import (
    get_input_icon,
    parse_input_update,
)
from custom_components.esp_weaver.iot.utils.light_utils import (
    build_light_turn_off_properties,
    build_light_turn_on_properties,
    convert_brightness_to_esp,
    convert_brightness_to_ha,
    parse_light_mode,
    parse_light_update,
)
from custom_components.esp_weaver.iot.utils.number_utils import (
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    format_range_for_log,
    format_value_for_log,
    get_device_threshold_param_name,
    get_number_range_config,
    get_sensor_entity_info,
    hpa_to_inhg,
    inhg_to_hpa,
    is_imperial_unit_system,
    parse_threshold_params,
)
from custom_components.esp_weaver.iot.utils.sensor_utils import (
    build_threshold_notification_data,
    check_threshold_violation,
    get_normalized_sensor_type,
    was_previously_violated,
)


class TestBrightnessConversion:
    """Test brightness conversion functions."""

    def test_convert_brightness_to_ha_zero(self) -> None:
        """Test converting ESP brightness 0 to HA."""
        assert convert_brightness_to_ha(0) == 0

    def test_convert_brightness_to_ha_max(self) -> None:
        """Test converting ESP brightness 100 to HA."""
        assert convert_brightness_to_ha(100) == 255

    def test_convert_brightness_to_ha_mid(self) -> None:
        """Test converting ESP brightness 50 to HA."""
        result = convert_brightness_to_ha(50)
        assert 127 <= result <= 128  # Allow for rounding

    def test_convert_brightness_to_esp_zero(self) -> None:
        """Test converting HA brightness 0 to ESP."""
        assert convert_brightness_to_esp(0) == 0

    def test_convert_brightness_to_esp_max(self) -> None:
        """Test converting HA brightness 255 to ESP."""
        assert convert_brightness_to_esp(255) == 100

    def test_convert_brightness_to_esp_mid(self) -> None:
        """Test converting HA brightness 128 to ESP."""
        result = convert_brightness_to_esp(128)
        assert 49 <= result <= 51  # Allow for rounding

    def test_brightness_round_trip(self) -> None:
        """Test brightness conversion round trip."""
        # Should be close to original after round trip
        original = 75
        ha_value = convert_brightness_to_ha(original)
        esp_value = convert_brightness_to_esp(ha_value)
        assert abs(esp_value - original) <= 1

    def test_convert_brightness_to_ha_negative_clamped(self) -> None:
        """Test ESP brightness below 0 is clamped to 0."""
        result = convert_brightness_to_ha(-10)
        assert result == 0

    def test_convert_brightness_to_ha_above_max_clamped(self) -> None:
        """Test ESP brightness above 100 is clamped to 255."""
        result = convert_brightness_to_ha(150)
        assert result == 255

    def test_convert_brightness_to_esp_negative_clamped(self) -> None:
        """Test HA brightness below 0 is clamped to 0."""
        result = convert_brightness_to_esp(-10)
        assert result == 0

    def test_convert_brightness_to_esp_above_max_clamped(self) -> None:
        """Test HA brightness above 255 is clamped to 100."""
        result = convert_brightness_to_esp(300)
        assert result == 100


class TestLightModeParsing:
    """Test light mode parsing functions."""

    def test_parse_light_mode_valid(self) -> None:
        """Test parsing valid mode string."""
        assert parse_light_mode("Mode 0") == 0
        assert parse_light_mode("Mode 3") == 3
        assert parse_light_mode("Mode 5") == 5

    def test_parse_light_mode_invalid_number(self) -> None:
        """Test parsing mode with invalid number."""
        assert parse_light_mode("Mode 6") is None  # Out of range
        assert parse_light_mode("Mode -1") is None

    def test_parse_light_mode_invalid_format(self) -> None:
        """Test parsing invalid format strings."""
        assert parse_light_mode("Invalid") is None
        assert parse_light_mode("") is None
        assert parse_light_mode("Mode") is None

    def test_parse_light_mode_value_error(self) -> None:
        """Test parsing mode with non-numeric value (ValueError)."""
        # "Mode abc" will cause ValueError when trying to parse "abc" as int
        assert parse_light_mode("Mode abc") is None

    def test_parse_light_mode_index_error(self) -> None:
        """Test parsing mode string that causes IndexError."""
        # Empty string after "Mode " will cause split to return fewer items
        assert parse_light_mode("Mode ") is None


class TestLightUpdateParsing:
    """Test light update parsing."""

    def test_parse_light_update_power(self) -> None:
        """Test parsing power update."""
        light_data = {"power": True}
        current_state = {"is_on": False}

        result = parse_light_update(light_data, current_state)

        assert result["is_on"] is True

    def test_parse_light_update_brightness(self) -> None:
        """Test parsing brightness update."""
        light_data = {"brightness": 75}
        current_state = {}

        result = parse_light_update(light_data, current_state)

        assert result["brightness"] == convert_brightness_to_ha(75)

    def test_parse_light_update_partial_color(self) -> None:
        """Test parsing partial HS color update."""
        light_data = {"hue": 120}
        current_state = {"hs_color": (60, 80)}

        result = parse_light_update(light_data, current_state)

        # Hue updated, saturation preserved
        assert result["hs_color"] == (120, 80)

    def test_parse_light_update_empty(self) -> None:
        """Test parsing empty update."""
        result = parse_light_update({}, {})
        assert result == {}


class TestLightPropertyBuilders:
    """Test light property builder functions."""

    def test_build_light_turn_on_basic(self) -> None:
        """Test building basic turn on properties."""
        result = build_light_turn_on_properties()

        assert result["Power"] is True

    def test_build_light_turn_on_with_brightness(self) -> None:
        """Test building turn on with brightness."""
        result = build_light_turn_on_properties(brightness=200)

        assert result["Power"] is True
        assert result["Brightness"] == convert_brightness_to_esp(200)

    def test_build_light_turn_on_with_hs_color(self) -> None:
        """Test building turn on with HS color."""
        result = build_light_turn_on_properties(hs_color=(180.0, 75.0))

        assert result["Power"] is True
        assert result["Hue"] == 180
        assert result["Saturation"] == 75

    def test_build_light_turn_on_with_effect(self) -> None:
        """Test building turn on with effect."""
        result = build_light_turn_on_properties(effect="Mode 2")

        assert result["Power"] is True
        assert result["Light Mode"] == 2

    def test_build_light_turn_off(self) -> None:
        """Test building turn off properties."""
        result = build_light_turn_off_properties()

        assert result["Power"] is False


class TestBinarySensorDeviceClass:
    """Test binary sensor device class functions."""

    def test_get_device_class_motion(self) -> None:
        """Test getting motion device class."""
        result = get_binary_sensor_device_class("motion")
        assert result == "motion"

    def test_get_device_class_touch_to_occupancy(self) -> None:
        """Test touch device class maps to occupancy."""
        result = get_binary_sensor_device_class("touch")
        assert result == "occupancy"

    def test_get_device_class_case_insensitive(self) -> None:
        """Test device class is case insensitive."""
        result = get_binary_sensor_device_class("MOTION")
        assert result == "motion"

    def test_get_device_class_default(self) -> None:
        """Test default device class for unknown type."""
        result = get_binary_sensor_device_class("unknown_type")
        assert result == "door"  # Default is door

    def test_get_device_class_empty(self) -> None:
        """Test default for empty string."""
        result = get_binary_sensor_device_class("")
        assert result == "door"  # Default is door


class TestBinarySensorUpdateProcessing:
    """Test binary sensor update processing."""

    def test_process_update_state_change(self) -> None:
        """Test processing state change."""
        state_data = {"sensor_value": True}  # Uses sensor_value key
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )

        assert result.state is True
        assert result.has_changes is True

    def test_process_update_no_change(self) -> None:
        """Test processing with no state change."""
        state_data = {"sensor_value": True}
        result = process_binary_sensor_update(
            state_data,
            current_state=True,
            current_device_class="door",
        )

        assert result.state is None
        assert result.has_changes is False

    def test_process_update_with_config(self) -> None:
        """Test processing with debounce and interval."""
        state_data = {
            "sensor_value": True,
            "params": {
                "debounce_time": 100,
                "report_interval": 5000,
            },
        }
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )

        assert result.debounce_time == 100
        assert result.report_interval == 5000
        assert result.has_changes is True

    def test_process_update_negative_debounce_time(self) -> None:
        """Test processing with negative debounce time."""
        state_data = {
            "params": {
                "debounce_time": -100,  # Invalid negative value
            },
        }
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )

        # Negative debounce_time should be ignored
        assert result.debounce_time is None
        assert result.has_changes is False

    def test_process_update_invalid_debounce_time(self) -> None:
        """Test processing with invalid debounce time."""
        state_data = {
            "params": {
                "debounce_time": "invalid",  # Non-numeric value
            },
        }
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )

        # Invalid debounce_time should be ignored
        assert result.debounce_time is None

    def test_process_update_negative_report_interval(self) -> None:
        """Test processing with negative report interval."""
        state_data = {
            "params": {
                "report_interval": -5000,  # Invalid negative value
            },
        }
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )

        # Negative report_interval should be ignored
        assert result.report_interval is None
        assert result.has_changes is False

    def test_process_update_invalid_report_interval(self) -> None:
        """Test processing with invalid report interval."""
        state_data = {
            "params": {
                "report_interval": "invalid",  # Non-numeric value
            },
        }
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )

        # Invalid report_interval should be ignored
        assert result.report_interval is None

    def test_get_device_class_whitespace_only(self) -> None:
        """Test device class with whitespace only returns default."""
        result = get_binary_sensor_device_class("   ")
        assert result == "door"  # Default is door


class TestSensorThresholdUtils:
    """Test sensor threshold utility functions."""

    def test_check_threshold_violation_high(self) -> None:
        """Test detecting high threshold violation."""
        violation_type, threshold = check_threshold_violation(35.0, 10.0, 30.0)
        assert violation_type == "high"
        assert threshold == 30.0

    def test_check_threshold_violation_low(self) -> None:
        """Test detecting low threshold violation."""
        violation_type, threshold = check_threshold_violation(5.0, 10.0, 30.0)
        assert violation_type == "low"
        assert threshold == 10.0

    def test_check_threshold_violation_none(self) -> None:
        """Test no violation when value is in range."""
        violation_type, threshold = check_threshold_violation(20.0, 10.0, 30.0)
        assert violation_type is None
        assert threshold is None

    def test_check_threshold_violation_no_thresholds(self) -> None:
        """Test no violation when thresholds not set."""
        violation_type, threshold = check_threshold_violation(50.0, None, None)
        assert violation_type is None
        assert threshold is None

    def test_was_previously_violated_true(self) -> None:
        """Test detecting previous violation."""
        assert was_previously_violated(35.0, 10.0, 30.0) is True
        assert was_previously_violated(5.0, 10.0, 30.0) is True

    def test_was_previously_violated_false(self) -> None:
        """Test detecting no previous violation."""
        assert was_previously_violated(20.0, 10.0, 30.0) is False
        assert was_previously_violated(None, 10.0, 30.0) is False

    def test_get_normalized_sensor_type(self) -> None:
        """Test sensor type normalization."""
        # Should handle case variations
        result = get_normalized_sensor_type("TEMPERATURE")
        assert result == "temperature"

    def test_build_threshold_notification_data(self) -> None:
        """Test building notification data."""
        data = build_threshold_notification_data(
            device_name="Test Device",
            node_id="test123",
            sensor_type="temperature",
            alert_type="high",
            current_value=35.0,
            threshold_value=30.0,
            unit="°C",
            domain="esp_weaver",
        )

        assert "notification_id" in data
        assert "title" in data
        assert "message" in data
        assert "test123" in data["notification_id"]
        assert "temperature" in data["notification_id"]
        assert "high" in data["notification_id"]


# =============================================================================
# Battery Utilities
# =============================================================================
class TestBatteryUtilities:
    """Test battery utility functions."""

    def test_get_battery_icon_full(self) -> None:
        """Test battery icon for full battery."""
        icon = get_battery_icon(100, "discharging", "normal")
        assert icon == ICON_BATTERY_FULL

    def test_get_battery_icon_low(self) -> None:
        """Test battery icon for low battery (below 25%)."""
        # Battery level 20 is below BATTERY_LEVEL_LOW (25), so should show battery-10
        icon = get_battery_icon(20, "discharging", "normal")
        assert icon == ICON_BATTERY_10

    def test_get_battery_icon_charging(self) -> None:
        """Test battery icon when charging."""
        icon = get_battery_icon(50, "charging", "normal")
        assert icon == ICON_BATTERY_CHARGING

    def test_get_battery_icon_alert(self) -> None:
        """Test battery icon with critical alert."""
        icon = get_battery_icon(10, "discharging", "critical")
        assert icon == ICON_BATTERY_ALERT

    def test_parse_battery_update_empty(self) -> None:
        """Test parsing empty battery update returns empty dict."""
        result = parse_battery_update({})
        assert result == {}

    def test_parse_battery_update_with_data(self) -> None:
        """Test parsing battery update with valid data extracts fields."""
        data = {
            KEY_BATTERY_LEVEL: 75,
            KEY_VOLTAGE: 3800,  # millivolts
        }
        result = parse_battery_update(data)
        assert result[KEY_BATTERY_LEVEL] == 75
        assert result[KEY_VOLTAGE] == 3.8  # converted to volts

    def test_build_battery_notification_data_normal(self) -> None:
        """Test building notification data for normal battery returns None."""
        # Normal alert level (not low or critical) should return None
        result = build_battery_notification_data("Device", "node1", "normal", 80)
        assert result is None

    def test_build_battery_notification_data_low(self) -> None:
        """Test building notification data for low battery returns notification."""
        result = build_battery_notification_data("Device", "node1", "low", 20)
        assert result is not None
        assert "notification_id" in result
        assert "title" in result
        assert "message" in result
        assert "node1" in result["notification_id"]

    def test_get_battery_notification_ids_to_clear(self) -> None:
        """Test getting notification IDs to clear returns expected IDs."""
        ids = get_battery_notification_ids_to_clear("node1")
        assert len(ids) == 2
        assert "esp_battery_critical_node1" in ids
        assert "esp_battery_low_node1" in ids


class TestBatteryVoltageProcessing:
    """Test battery voltage parsing."""

    def test_parse_voltage_millivolts(self) -> None:
        """Test parsing voltage in millivolts."""
        processor = BatteryProcessor()
        result = processor._parse_voltage(3800)
        assert result == 3.8

    def test_parse_voltage_invalid_type(self) -> None:
        """Test parsing voltage with invalid type."""
        processor = BatteryProcessor()
        result = processor._parse_voltage("invalid")
        assert result is None

    def test_parse_voltage_out_of_range(self) -> None:
        """Test parsing voltage out of valid range."""
        processor = BatteryProcessor()
        # Too high
        assert processor._parse_voltage(6000) is None
        # Too low
        assert processor._parse_voltage(1500) is None


# =============================================================================
# Gesture Utilities
# =============================================================================
class TestGestureUtilities:
    """Test gesture utility functions."""

    def test_gesture_processor_init(self) -> None:
        """Test GestureProcessor has valid GESTURE_CONFIGS."""
        processor = GestureProcessor()
        assert len(processor.GESTURE_CONFIGS) > 0
        assert "idle" in processor.GESTURE_CONFIGS

    def test_gesture_processor_initialize_events(self) -> None:
        """Test initializing gesture events creates all event flags as False."""
        processor = GestureProcessor()
        events = processor.initialize_events()
        # Should have event flags for each gesture type with event_attr
        assert "shake_event" in events
        assert all(v is False for v in events.values())

    def test_gesture_processor_reset_events(self) -> None:
        """Test resetting gesture events sets all to False."""
        processor = GestureProcessor()
        events = {"shake_event": True, "push_event": True, "flip_event": False}
        result = processor.reset_events(events)
        assert all(v is False for v in result.values())

    def test_gesture_processor_process_update_empty(self) -> None:
        """Test processing empty update preserves current gesture."""
        processor = GestureProcessor()
        events = processor.initialize_events()
        result = processor.process_update(
            sensor_data={},
            previous_events=events,
            current_gesture="idle",
            current_display_duration=2.0,
        )
        assert result.gesture == "idle"
        assert result.gesture_triggered is False
        assert result.display_duration == 2.0

    def test_gesture_processor_process_update_with_gesture(self) -> None:
        """Test processing update with gesture type triggers gesture."""
        processor = GestureProcessor()
        events = processor.initialize_events()
        result = processor.process_update(
            sensor_data={KEY_GESTURE_TYPE: "shake", KEY_GESTURE_CONFIDENCE: 90},
            previous_events=events,
            current_gesture="idle",
            current_display_duration=2.0,
        )
        assert result.gesture == "shake"
        assert result.confidence == 90
        assert result.gesture_triggered is True

    def test_normalize_gesture(self) -> None:
        """Test normalizing gesture types."""
        processor = GestureProcessor()
        assert processor.normalize_gesture(None) == "idle"
        assert processor.normalize_gesture("") == "idle"
        assert processor.normalize_gesture("none") == "idle"

    def test_parse_event_value(self) -> None:
        """Test parsing event values."""
        processor = GestureProcessor()
        assert processor.parse_event_value(True) is True
        assert processor.parse_event_value(False) is False
        assert processor.parse_event_value(1) is True
        assert processor.parse_event_value(0) is False
        assert processor.parse_event_value("true") is True
        assert processor.parse_event_value(None) is False

    def test_parse_confidence(self) -> None:
        """Test parsing confidence values."""
        processor = GestureProcessor()
        assert processor.parse_confidence(75) == 75
        assert processor.parse_confidence(150) == 100
        assert processor.parse_confidence(-10) == 0
        assert processor.parse_confidence(None) == 0

    def test_get_gesture_icon(self) -> None:
        """Test getting gesture icon."""
        icon = get_gesture_icon("shake")
        assert icon.startswith("mdi:")
        icon = get_gesture_icon("unknown_gesture")
        assert icon.startswith("mdi:")


# =============================================================================
# Input Utilities
# =============================================================================
class TestInputUtilities:
    """Test input utility functions."""

    def test_get_input_icon_button(self) -> None:
        """Test input icon for button."""
        icon = get_input_icon("button", "press")
        assert icon.startswith("mdi:")

    def test_get_input_icon_encoder(self) -> None:
        """Test input icon for encoder."""
        icon = get_input_icon("encoder", "rotate")
        assert icon.startswith("mdi:")

    def test_parse_input_update_empty(self) -> None:
        """Test parsing empty input update."""
        result = parse_input_update({})
        assert isinstance(result, dict)

    def test_parse_input_update_with_data(self) -> None:
        """Test parsing input update with data."""
        data = {
            "input_type": "button",
            "last_event": "click",
            "input_value": 1,
        }
        result = parse_input_update(data)
        assert isinstance(result, dict)

    def test_parse_input_update_with_events(self) -> None:
        """Test parsing input update with input_events field."""
        data = {"input_events": "double_click"}
        result = parse_input_update(data)
        assert result.get("last_event") == "double_click"


# =============================================================================
# Number Utilities
# =============================================================================
class TestNumberUtilities:
    """Test number utility functions."""

    def test_get_number_range_config_temperature(self) -> None:
        """Test getting number range config for temperature."""
        config = get_number_range_config("temperature", "min")
        assert isinstance(config, dict)
        assert "min" in config
        assert "max" in config
        assert config["min"] < config["max"]

    def test_get_number_range_config_humidity(self) -> None:
        """Test getting number range config for humidity."""
        config = get_number_range_config("humidity", "max")
        assert isinstance(config, dict)
        assert "min" in config
        assert "max" in config

    def test_get_number_range_config_unknown(self) -> None:
        """Test getting number range config for unknown sensor."""
        config = get_number_range_config("unknown_sensor", "min")
        assert isinstance(config, dict)

    def test_get_device_threshold_param_name(self) -> None:
        """Test getting device threshold param name."""
        name = get_device_threshold_param_name("temperature", "min")
        assert isinstance(name, str)
        assert "min" in name

    def test_parse_threshold_params(self) -> None:
        """Test parsing threshold params."""
        data = {
            "temp_min_threshold": 10,
            "temp_max_threshold": 30,
            "humidity_min_threshold": 20,
        }
        sensor_types = parse_threshold_params(data)
        assert isinstance(sensor_types, set)

    def test_parse_threshold_params_empty(self) -> None:
        """Test parsing empty threshold params."""
        sensor_types = parse_threshold_params({})
        assert isinstance(sensor_types, set)
        assert len(sensor_types) == 0

    def test_get_sensor_entity_info_with_attrs(self) -> None:
        """Test getting sensor entity info with attributes."""
        mock_entity = MagicMock()
        mock_entity._sensor_type = "temperature"
        mock_entity._node_id = "node123"
        mock_entity.name = "Test Sensor"

        info = get_sensor_entity_info(mock_entity)
        assert info is not None
        assert info["sensor_type"] == "temperature"
        assert info["node_id"] == "node123"

    def test_get_sensor_entity_info_missing_attrs(self) -> None:
        """Test getting sensor entity info with missing attributes."""
        mock_entity = MagicMock()
        mock_entity._sensor_type = ""
        mock_entity._node_id = ""

        info = get_sensor_entity_info(mock_entity)
        assert info is None

    def test_get_number_range_config_empty_sensor_type(self) -> None:
        """Test getting number range config with empty sensor type."""
        config = get_number_range_config("", "min")
        # Should return default config
        assert isinstance(config, dict)
        assert config["min"] == 0.0
        assert config["max"] == 100.0

    def test_get_number_range_config_invalid_threshold_type(self) -> None:
        """Test getting number range config with invalid threshold type."""
        config = get_number_range_config("temperature", "invalid")
        # Should return default config
        assert isinstance(config, dict)
        assert "min" in config
        assert "max" in config

    def test_get_device_threshold_param_name_empty_sensor_type(self) -> None:
        """Test getting device threshold param name with empty sensor type."""
        with pytest.raises(ValueError, match="sensor_type must be provided"):
            get_device_threshold_param_name("", "min")

    def test_get_device_threshold_param_name_invalid_threshold_type(self) -> None:
        """Test getting device threshold param name with invalid threshold type."""
        # Should default to "min"
        name = get_device_threshold_param_name("temperature", "invalid")
        assert "min" in name

    def test_parse_threshold_params_none(self) -> None:
        """Test parsing None threshold params."""
        sensor_types = parse_threshold_params(None)
        assert isinstance(sensor_types, set)
        assert len(sensor_types) == 0

    def test_get_sensor_entity_info_with_none_name(self) -> None:
        """Test getting sensor entity info when name is None."""
        mock_entity = MagicMock()
        mock_entity._sensor_type = "temperature"
        mock_entity._node_id = "node123"
        mock_entity.name = None

        info = get_sensor_entity_info(mock_entity)
        assert info is not None
        # When name is None, sensor_name will be None
        assert info["sensor_name"] is None

    def test_get_sensor_entity_info_none_sensor_type(self) -> None:
        """Test getting sensor entity info with None sensor type."""
        mock_entity = MagicMock()
        mock_entity._sensor_type = None
        mock_entity._node_id = "node123"

        info = get_sensor_entity_info(mock_entity)
        assert info is None


# =============================================================================
# Additional Battery Utilities Tests
# =============================================================================
class TestBatteryUtilitiesExtended:
    """Extended tests for battery utility functions."""

    def test_battery_processor_parse_update_invalid_battery_level(self) -> None:
        """Test parsing battery update with invalid battery level."""
        processor = BatteryProcessor()
        # Level out of range
        state = processor.parse_update({KEY_BATTERY_LEVEL: 150})
        assert state.battery_level is None

    def test_battery_processor_parse_update_non_numeric_level(self) -> None:
        """Test parsing battery update with non-numeric battery level."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_BATTERY_LEVEL: "invalid"})
        assert state.battery_level is None

    def test_battery_processor_parse_update_temperature_out_of_range(self) -> None:
        """Test parsing battery update with temperature out of range."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_TEMPERATURE: 150.0})
        assert state.temperature is None

    def test_battery_processor_parse_update_invalid_temperature(self) -> None:
        """Test parsing battery update with invalid temperature."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_TEMPERATURE: "invalid"})
        assert state.temperature is None

    def test_battery_processor_voltage_negative(self) -> None:
        """Test parsing negative voltage value."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_VOLTAGE: -3.7})
        assert state.voltage is None

    def test_battery_processor_voltage_ambiguous_range(self) -> None:
        """Test parsing voltage in ambiguous range (100-1000)."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_VOLTAGE: 500})
        assert state.voltage is None

    def test_battery_processor_voltage_marker_value(self) -> None:
        """Test parsing voltage with marker value (0xFFFFFFFF)."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_VOLTAGE: 4294967295})
        assert state.voltage is None

    def test_battery_processor_voltage_out_of_safe_range(self) -> None:
        """Test parsing voltage out of safe range."""
        processor = BatteryProcessor()
        state = processor.parse_update({KEY_VOLTAGE: 6000})  # 6V
        assert state.voltage is None

    def test_battery_icon_low_alert(self) -> None:
        """Test battery icon with low alert level."""
        icon = get_battery_icon(50, "discharging", "low")
        assert icon == ICON_BATTERY_LOW

    def test_battery_icon_medium_level(self) -> None:
        """Test battery icon for medium battery level (26-50%)."""
        # Battery level 30 is in the medium range (BATTERY_LEVEL_LOW < 30 <= BATTERY_LEVEL_MEDIUM)
        icon = get_battery_icon(30, "discharging", "normal")
        assert icon == ICON_BATTERY_30

    def test_build_battery_notification_data_critical(self) -> None:
        """Test building notification data for critical battery returns notification."""
        result = build_battery_notification_data("Device", "node1", "critical", 5)
        assert result is not None
        assert "notification_id" in result
        assert "Critical" in result["title"]
        assert "immediately" in result["message"]


# =============================================================================
# Additional Gesture Utilities Tests
# =============================================================================
class TestGestureUtilitiesExtended:
    """Extended tests for gesture utility functions."""

    def test_gesture_processor_parse_confidence_invalid(self) -> None:
        """Test parsing invalid confidence value."""
        processor = GestureProcessor()
        assert processor.parse_confidence("invalid") == 0
        assert processor.parse_confidence(None) == 0

    def test_gesture_processor_parse_orientation_passthrough(self) -> None:
        """Test orientation value is passed through without conversion."""
        processor = GestureProcessor()
        assert processor.parse_orientation("invalid") == "invalid"
        assert processor.parse_orientation(None) is None
        assert processor.parse_orientation(45.3) == 45.3
        assert processor.parse_orientation(90) == 90

    def test_gesture_processor_process_update_multiple_triggers(self) -> None:
        """Test processing update with multiple gestures triggered."""
        processor = GestureProcessor()
        previous_events = processor.initialize_events()

        # Simulate multiple event flags changing to True at once
        sensor_data = {
            "shake_event": True,
            "push_event": True,
        }

        result = processor.process_update(sensor_data, previous_events)
        # Should use the last triggered gesture (push is processed after shake)
        assert result.gesture_triggered is True
        assert result.gesture == "push"

    def test_gesture_processor_process_update_display_duration_invalid(self) -> None:
        """Test processing update with invalid display duration."""
        processor = GestureProcessor()
        previous_events = processor.initialize_events()

        sensor_data = {KEY_GESTURE_DISPLAY_DURATION: "invalid"}

        result = processor.process_update(
            sensor_data, previous_events, current_display_duration=3.0
        )
        # Should keep current_display_duration
        assert result.display_duration == 3.0

    def test_gesture_processor_process_update_sensitivity_invalid(self) -> None:
        """Test processing update with invalid sensitivity."""
        processor = GestureProcessor()
        previous_events = processor.initialize_events()

        sensor_data = {KEY_SENSITIVITY: "invalid"}

        result = processor.process_update(
            sensor_data, previous_events, current_sensitivity=60
        )
        # Should keep current_sensitivity
        assert result.sensitivity == 60

    def test_gesture_processor_process_update_orientation_change(self) -> None:
        """Test processing update with orientation change field."""
        processor = GestureProcessor()
        previous_events = processor.initialize_events()

        sensor_data = {KEY_ORIENTATION_CHANGE: 45.0}

        result = processor.process_update(sensor_data, previous_events)
        assert KEY_ORIENTATION_CHANGE_SHORT in result.orientation
        assert result.orientation[KEY_ORIENTATION_CHANGE_SHORT] == 45.0

    def test_gesture_icon_unknown_gesture(self) -> None:
        """Test getting icon for unknown gesture type."""
        icon = get_gesture_icon("unknown_gesture")
        assert icon == DEFAULT_GESTURE_ICON

    def test_gesture_processor_parse_event_value_string_variations(self) -> None:
        """Test parsing event value with various string values."""
        processor = GestureProcessor()
        assert processor.parse_event_value("yes") is True
        assert processor.parse_event_value("on") is True
        assert processor.parse_event_value("false") is False
        assert processor.parse_event_value("no") is False

    def test_gesture_processor_parse_event_value_other_types(self) -> None:
        """Test parsing event value with other types returns False."""
        processor = GestureProcessor()
        # List type should return False
        assert processor.parse_event_value([1, 2, 3]) is False
        # Dict type should return False
        assert processor.parse_event_value({"key": "value"}) is False


# =============================================================================
# Unit Conversion Functions
# =============================================================================
class TestUnitConversionFunctions:
    """Test unit conversion functions."""

    def test_celsius_to_fahrenheit(self) -> None:
        """Test Celsius to Fahrenheit conversion."""
        assert celsius_to_fahrenheit(0) == 32.0
        assert celsius_to_fahrenheit(100) == 212.0
        assert celsius_to_fahrenheit(25) == 77.0

    def test_fahrenheit_to_celsius(self) -> None:
        """Test Fahrenheit to Celsius conversion."""
        assert fahrenheit_to_celsius(32) == 0.0
        assert fahrenheit_to_celsius(212) == 100.0
        assert fahrenheit_to_celsius(77) == 25.0

    def test_hpa_to_inhg(self) -> None:
        """Test hPa to inHg conversion."""
        result = hpa_to_inhg(1013.25)
        assert 29.9 <= result <= 30.0  # Standard atmosphere

    def test_inhg_to_hpa(self) -> None:
        """Test inHg to hPa conversion."""
        result = inhg_to_hpa(29.92)
        assert 1012 <= result <= 1015  # Standard atmosphere

    def test_is_imperial_unit_system_metric(self) -> None:
        """Test is_imperial_unit_system returns False for metric."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()  # Not US_CUSTOMARY_SYSTEM
        assert is_imperial_unit_system(mock_hass) is False

    def test_is_imperial_unit_system_attribute_error(self) -> None:
        """Test is_imperial_unit_system handles AttributeError."""
        mock_hass = MagicMock()
        mock_hass.config = None  # Will cause AttributeError
        assert is_imperial_unit_system(mock_hass) is False


class TestFormatValueForLog:
    """Test format_value_for_log function."""

    def test_format_temperature_metric(self) -> None:
        """Test formatting temperature in metric units."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()  # Not US_CUSTOMARY_SYSTEM
        result = format_value_for_log(mock_hass, 25.0, "temperature", 1)
        assert "25.0" in result
        assert "°C" in result

    def test_format_pressure_metric(self) -> None:
        """Test formatting pressure in metric units."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_value_for_log(mock_hass, 1013.0, "pressure", 1)
        assert "1013.0" in result
        assert "hPa" in result

    def test_format_illuminance_metric(self) -> None:
        """Test formatting illuminance in metric units."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_value_for_log(mock_hass, 500, "illuminance", 0)
        assert "500" in result
        assert "lx" in result

    def test_format_humidity(self) -> None:
        """Test formatting humidity (no conversion needed)."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_value_for_log(mock_hass, 65.5, "humidity", 1)
        assert "65.5" in result


class TestFormatRangeForLog:
    """Test format_range_for_log function."""

    def test_format_temperature_range_metric(self) -> None:
        """Test formatting temperature range in metric units."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_range_for_log(mock_hass, 10.0, 30.0, 0.5, 20.0, "temperature")
        assert "range" in result
        assert "10.0" in result
        assert "30.0" in result
        assert "°C" in result

    def test_format_pressure_range_metric(self) -> None:
        """Test formatting pressure range in metric units."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_range_for_log(mock_hass, 950.0, 1050.0, 1.0, 1013.0, "pressure")
        assert "range" in result
        assert "hPa" in result

    def test_format_illuminance_range_metric(self) -> None:
        """Test formatting illuminance range in metric units."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_range_for_log(
            mock_hass, 0.0, 1000.0, 10.0, 500.0, "illuminance"
        )
        assert "range" in result
        assert "lx" in result

    def test_format_humidity_range(self) -> None:
        """Test formatting humidity range (no conversion)."""
        mock_hass = MagicMock()
        mock_hass.config.units = MagicMock()
        result = format_range_for_log(mock_hass, 20.0, 80.0, 5.0, 50.0, "humidity")
        assert "range" in result


class TestFormatRangeForLogImperial:
    """Test format_range_for_log with imperial units."""

    def test_format_temperature_range_imperial(self) -> None:
        """Test formatting temperature range in imperial units."""

        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        result = format_range_for_log(mock_hass, 10.0, 30.0, 0.5, 20.0, "temperature")
        assert "range" in result
        assert "°F" in result

    def test_format_pressure_range_imperial(self) -> None:
        """Test formatting pressure range in imperial units."""

        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        result = format_range_for_log(mock_hass, 950.0, 1050.0, 1.0, 1013.0, "pressure")
        assert "range" in result
        assert "inHg" in result

    def test_format_illuminance_range_imperial(self) -> None:
        """Test formatting illuminance range in imperial units."""

        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        result = format_range_for_log(
            mock_hass, 100.0, 1000.0, 10.0, 500.0, "illuminance"
        )
        assert "range" in result
        # HA does not auto-convert lx to fc, so always use lx for consistency
        assert "lx" in result


class TestFormatValueForLogImperial:
    """Test format_value_for_log with imperial units."""

    def test_format_temperature_imperial(self) -> None:
        """Test formatting temperature in imperial units."""

        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        result = format_value_for_log(mock_hass, 25.0, "temperature", 1)
        assert "°F" in result
        assert "77.0" in result  # 25°C = 77°F

    def test_format_pressure_imperial(self) -> None:
        """Test formatting pressure in imperial units."""

        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        result = format_value_for_log(mock_hass, 1013.25, "pressure", 2)
        assert "inHg" in result

    def test_format_illuminance_imperial(self) -> None:
        """Test formatting illuminance in imperial units - always uses lx.

        HA does not auto-convert lx to fc for illuminance sensors,
        so we always use lx for consistency between UI and logs.
        """

        mock_hass = MagicMock()
        mock_hass.config.units = US_CUSTOMARY_SYSTEM
        result = format_value_for_log(mock_hass, 500.0, "illuminance", 1)
        # Always lx, no fc conversion
        assert "lx" in result
