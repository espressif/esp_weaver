# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver binary sensor platform."""

from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import Event, HomeAssistant
import pytest

from custom_components.esp_weaver.binary_sensor import (
    ESPWeaverBinarySensor,
    async_setup_entry,
)
from custom_components.esp_weaver.const import (
    ATTR_BINARY_SENSOR_TYPE,
    PLATFORM_BINARY_SENSOR,
)
from custom_components.esp_weaver.iot.specs.binary_sensor_specs import (
    DEFAULT_BINARY_SENSOR_DEVICE_CLASS,
)
from custom_components.esp_weaver.iot.specs.events import (
    DOMAIN,
    EVENT_BINARY_SENSOR_UPDATE,
)
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_DEBOUNCE_TIME,
    KEY_REPORT_INTERVAL,
)
from custom_components.esp_weaver.iot.utils.binary_sensor_utils import (
    BinarySensorUpdateResult,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestBinarySensorEntity:
    """Test ESPWeaverBinarySensor entity."""

    async def test_binary_sensor_initialization(
        self,
        mock_coordinator: MagicMock,
        mock_binary_sensor_data: dict[str, Any],
    ) -> None:
        """Test binary sensor entity initialization."""
        # Verify fixture provides expected device_class for deterministic test
        fixture_params = mock_binary_sensor_data.get("params", {})
        assert fixture_params.get("device_class") == "motion", (
            "Fixture mock_binary_sensor_data must have device_class='motion' for this test"
        )

        sensor_params = fixture_params.copy()
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params=sensor_params,
        )

        expected_unique_id = f"{DOMAIN}_{TEST_NODE_ID}_{PLATFORM_BINARY_SENSOR}"
        assert sensor._attr_unique_id == expected_unique_id
        assert sensor._attr_device_class == BinarySensorDeviceClass.MOTION
        assert sensor._attr_is_on is False

    async def test_binary_sensor_default_device_class(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test binary sensor with default device class."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={},
        )

        # Default is DOOR (DEFAULT_BINARY_SENSOR_DEVICE_CLASS)
        assert sensor._attr_device_class == BinarySensorDeviceClass.DOOR

    async def test_binary_sensor_is_on(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test binary sensor is_on property."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"state": True},
        )

        assert sensor._attr_is_on is True

    async def test_binary_sensor_availability(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test binary sensor availability property."""
        mock_coordinator.last_update_success = True
        mock_coordinator.is_available = True

        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        assert sensor.available is True

        mock_coordinator.is_available = False
        assert sensor.available is False

    async def test_binary_sensor_extra_attributes(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test binary sensor extra state attributes."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"device_class": "motion"},
        )

        attrs = sensor.extra_state_attributes
        assert ATTR_BINARY_SENSOR_TYPE in attrs
        assert attrs[ATTR_BINARY_SENSOR_TYPE] == "motion"


class TestBinarySensorUpdateHandling:
    """Test binary sensor update handling."""

    async def test_handle_binary_sensor_update_state_change(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling binary sensor state update."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"state": False, "device_class": "motion"},
        )
        sensor.hass = hass
        # Mock async_write_ha_state to avoid entity registration issues
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_BINARY_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_value": True,
            },
        )

        sensor._handle_binary_sensor_update(event)

        assert sensor._attr_is_on is True
        sensor.async_write_ha_state.assert_called_once()

    async def test_handle_binary_sensor_update_with_config(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling binary sensor update with debounce and interval."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"state": False, "device_class": "motion"},
        )
        sensor.hass = hass
        # Mock async_write_ha_state to avoid entity registration issues
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_BINARY_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_value": True,
                "params": {
                    "debounce_time": 100,
                    "report_interval": 5000,
                },
            },
        )

        sensor._handle_binary_sensor_update(event)

        assert sensor._attr_is_on is True
        attrs = sensor.extra_state_attributes
        assert KEY_DEBOUNCE_TIME in attrs
        assert KEY_REPORT_INTERVAL in attrs

    async def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test update for other node is ignored."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={
                "state": False,
                "debounce_time": 50,
                "report_interval": 1000,
            },
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        # Capture initial extra_state_attributes
        initial_attrs = sensor.extra_state_attributes.copy()

        event = Event(
            event_type=EVENT_BINARY_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: "different_node",
                "sensor_value": True,
            },
        )

        sensor._handle_binary_sensor_update(event)

        # State should not change
        assert sensor._attr_is_on is False
        sensor.async_write_ha_state.assert_not_called()
        # Verify extra_state_attributes remain unchanged
        assert sensor.extra_state_attributes == initial_attrs


class TestBinarySensorDeviceClasses:
    """Test binary sensor device class handling."""

    @pytest.mark.parametrize(
        ("device_class_input", "expected_class"),
        [
            # Valid mappings from BINARY_SENSOR_DEVICE_CLASS_MAP
            ("motion", BinarySensorDeviceClass.MOTION),
            ("door", BinarySensorDeviceClass.DOOR),
            ("plug", BinarySensorDeviceClass.PLUG),
            ("vibration", BinarySensorDeviceClass.VIBRATION),
            # "touch" maps to "occupancy"
            ("touch", BinarySensorDeviceClass.OCCUPANCY),
            # Unknown types default to DOOR
            ("unknown", BinarySensorDeviceClass.DOOR),
            (
                "window",
                BinarySensorDeviceClass.DOOR,
            ),  # Not in mapping, defaults to door
            # Case-sensitivity checks - mixed case should still map correctly
            ("Motion", BinarySensorDeviceClass.MOTION),
            ("MOTION", BinarySensorDeviceClass.MOTION),
            ("Door", BinarySensorDeviceClass.DOOR),
            ("DOOR", BinarySensorDeviceClass.DOOR),
            ("Touch", BinarySensorDeviceClass.OCCUPANCY),
            ("TOUCH", BinarySensorDeviceClass.OCCUPANCY),
        ],
    )
    async def test_device_class_mapping(
        self,
        mock_coordinator: MagicMock,
        device_class_input: str,
        expected_class: BinarySensorDeviceClass,
    ) -> None:
        """Test various device class mappings."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"device_class": device_class_input},
        )

        assert sensor._attr_device_class == expected_class


class TestBinarySensorPlatformSetup:
    """Test binary sensor platform setup."""

    async def test_setup_entry_registers_discovery_listener(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that setup registers discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.binary_sensor.setup_platform_discovery"
            ) as mock_setup,
            patch(
                "custom_components.esp_weaver.binary_sensor.setup_single_entity_discovery"
            ) as mock_discovery,
        ):
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

            mock_setup.assert_called_once()
            mock_discovery.assert_called_once()
            # Verify async_add_entities was passed to discovery helpers
            setup_call_kwargs = mock_setup.call_args
            assert setup_call_kwargs is not None
            assert (
                setup_call_kwargs.kwargs.get("async_add_entities") == async_add_entities
            )


class TestBinarySensorEntityName:
    """Test binary sensor entity naming."""

    async def test_custom_name_from_params(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test custom name is used from params."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"name": "Custom Motion Sensor"},
        )

        assert sensor._attr_name == "Custom Motion Sensor"

    async def test_default_name_when_not_provided(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test default name is used when not provided."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={},
        )

        assert sensor._attr_name == "Binary Sensor"


class TestBinarySensorEdgeCases:
    """Test binary sensor edge cases and malformed data handling."""

    async def test_handle_update_missing_sensor_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling update with missing sensor_value key."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"state": False},
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        # Event with missing sensor_value
        event = Event(
            event_type=EVENT_BINARY_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                # Missing "sensor_value" key
            },
        )

        # Should not raise exception
        sensor._handle_binary_sensor_update(event)

        # State should not change
        assert sensor._attr_is_on is False
        sensor.async_write_ha_state.assert_not_called()

    async def test_handle_update_null_sensor_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling update with null sensor_value."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"state": True},
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_BINARY_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_value": None,
            },
        )

        # Should not raise exception
        sensor._handle_binary_sensor_update(event)

        # State should not change when value is None
        assert sensor._attr_is_on is True
        sensor.async_write_ha_state.assert_not_called()

    async def test_handle_update_wrong_type_sensor_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling update with wrong type sensor_value."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"state": False},
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        # Event with string value that should be bool
        event = Event(
            event_type=EVENT_BINARY_SENSOR_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_value": "not_a_bool",  # Wrong type
            },
        )

        # Should handle gracefully (Python treats non-empty strings as truthy)
        sensor._handle_binary_sensor_update(event)

        # Non-empty string is truthy, so state changes from False to True
        assert sensor._attr_is_on is True
        sensor.async_write_ha_state.assert_called_once()

    async def test_sensor_with_invalid_device_class_type(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor initialization with invalid device_class type uses default."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"device_class": 123},  # Invalid type
        )

        # Should use default device class
        assert sensor._attr_device_class == DEFAULT_BINARY_SENSOR_DEVICE_CLASS

    async def test_sensor_with_empty_string_device_class(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor initialization with empty string device_class uses default."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"device_class": ""},
        )

        # Empty string should use default
        assert sensor._attr_device_class == DEFAULT_BINARY_SENSOR_DEVICE_CLASS

    async def test_sensor_with_invalid_name_type(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor initialization with invalid name type."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"name": 123},  # Invalid type
        )

        # Should handle gracefully - convert to string
        assert sensor._attr_name is not None
        # Integer should be stringified
        assert sensor._attr_name == "123"

    async def test_sensor_with_invalid_enum_device_class(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test sensor initialization with device class not in BinarySensorDeviceClass enum."""
        # Mock get_binary_sensor_device_class to return an invalid enum value
        with patch(
            "custom_components.esp_weaver.binary_sensor.get_binary_sensor_device_class",
            return_value="invalid_enum_value",
        ):
            sensor = ESPWeaverBinarySensor(
                coordinator=mock_coordinator,
                node_id=TEST_NODE_ID,
                device_name=TEST_DEVICE_NAME,
                sensor_params={"device_class": "some_class"},
            )

        # ValueError is caught, device_class should be None
        assert sensor._attr_device_class is None
        assert sensor._binary_sensor_type is None

    async def test_handle_update_with_invalid_enum_device_class(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test _handle_binary_sensor_update with invalid device class in update."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={"device_class": "motion", "state": False},
        )
        sensor.hass = hass
        sensor.entity_id = "binary_sensor.test"
        sensor.async_write_ha_state = MagicMock()

        # Mock process_binary_sensor_update to return invalid device_class
        with patch(
            "custom_components.esp_weaver.binary_sensor.process_binary_sensor_update"
        ) as mock_process:
            mock_result = BinarySensorUpdateResult()
            mock_result.state = True
            mock_result.device_class = "invalid_enum_value"
            mock_result.has_changes = True
            mock_process.return_value = mock_result

            event = Event(
                EVENT_BINARY_SENSOR_UPDATE,
                {CONF_NODE_ID: TEST_NODE_ID},
            )
            sensor._handle_binary_sensor_update(event)

        # State should be updated
        assert sensor._attr_is_on is True
        # Invalid device_class triggers ValueError, both are cleared to None
        assert sensor._binary_sensor_type is None
        assert sensor._attr_device_class is None
