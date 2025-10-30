# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver event dispatcher module."""

from homeassistant.core import Event, HomeAssistant

from custom_components.esp_weaver.helpers.event_dispatcher import (
    create_event_dispatcher,
    fire_property_events,
)
from custom_components.esp_weaver.iot.specs.device_specs import (
    DEVICE_TYPE_BATTERY_ENERGY,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_IMU_GESTURE,
    DEVICE_TYPE_INTERACTIVE_INPUT,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_LOW_POWER_SLEEP,
    DEVICE_TYPE_TEMPERATURE_SENSOR,
)

from .conftest import TEST_NODE_ID


class TestCreateEventDispatcher:
    """Test create_event_dispatcher function."""

    async def test_creates_callable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that it creates a callable."""
        dispatcher = create_event_dispatcher(hass)

        assert callable(dispatcher)

    async def test_dispatcher_can_be_called(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that dispatcher can be called and fires events."""
        dispatcher = create_event_dispatcher(hass)
        events: list[Event] = []

        # Listen for light update event (dispatcher routes to specific event types)
        hass.bus.async_listen("esp_weaver_light_update", lambda e: events.append(e))

        # Use valid Light data (ESP property names use PascalCase)
        dispatcher(TEST_NODE_ID, {DEVICE_TYPE_LIGHT: {"Power": True}})
        await hass.async_block_till_done()

        # Verify event was actually fired
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID


class TestFirePropertyEvents:
    """Test fire_property_events function."""

    async def test_empty_params(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with empty params."""
        # Should not raise
        fire_property_events(hass, TEST_NODE_ID, {})
        await hass.async_block_till_done()

    async def test_with_light_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with light data fires event."""
        # Use ESP property names (PascalCase) as keys
        params = {
            DEVICE_TYPE_LIGHT: {
                "Power": True,
                "Brightness": 128,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen("esp_weaver_light_update", lambda e: events.append(e))

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify light update event was fired with correct payload
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID
        assert "light_data" in events[0].data
        assert events[0].data["light_data"]["brightness"] == 128
        assert events[0].data["light_data"]["power"] is True

    async def test_with_sensor_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with sensor data fires events (one per field)."""
        params = {
            DEVICE_TYPE_TEMPERATURE_SENSOR: {
                "temperature": 25.5,
                "humidity": 60.0,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen("esp_weaver_sensor_update", lambda e: events.append(e))

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify sensor update events were fired (one per sensor field)
        assert len(events) == 2
        assert all(e.data["node_id"] == TEST_NODE_ID for e in events)
        # Verify payload values (use "type" key from DATA_KEY_TYPE)
        sensor_values = {e.data["type"]: e.data["value"] for e in events}
        assert sensor_values["temperature"] == 25.5
        assert sensor_values["humidity"] == 60.0

    async def test_with_multiple_device_types(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with multiple device types."""
        params = {
            DEVICE_TYPE_LIGHT: {"Power": True},  # ESP property names use PascalCase
            DEVICE_TYPE_TEMPERATURE_SENSOR: {"temperature": 25.5},
        }
        light_events: list[Event] = []
        sensor_events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_light_update", lambda e: light_events.append(e)
        )
        hass.bus.async_listen(
            "esp_weaver_sensor_update", lambda e: sensor_events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        assert len(light_events) == 1
        assert len(sensor_events) == 1
        # Verify payload contents
        assert light_events[0].data["light_data"]["power"] is True
        assert sensor_events[0].data["value"] == 25.5

    async def test_with_unknown_device_type(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with unknown device type handles gracefully without firing events."""
        params = {
            "UnknownDevice": {"value": 123},
        }
        all_events: list[Event] = []

        # Listen for known event types that should NOT be fired
        for event_type in [
            "esp_weaver_light_update",
            "esp_weaver_sensor_update",
            "esp_weaver_binary_sensor_update",
            "esp_weaver_battery_energy_update",
            "esp_weaver_imu_gesture_update",
            "esp_weaver_interactive_input_update",
            "esp_weaver_low_power_sleep_update",
        ]:
            hass.bus.async_listen(event_type, lambda e: all_events.append(e))

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify no events were fired for unknown device type
        assert len(all_events) == 0

    async def test_with_threshold_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with threshold data in sensor fires threshold events."""
        # Use correct threshold key names: temp_*_threshold (not temperature_*_threshold)
        # as defined in SENSOR_DEFINITIONS with device_param_prefix="temp"
        params = {
            DEVICE_TYPE_TEMPERATURE_SENSOR: {
                "temp_min_threshold": 10.0,
                "temp_max_threshold": 30.0,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_threshold_data_received", lambda e: events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify threshold events were fired (one for min, one for max)
        assert len(events) == 2
        assert all(e.data["node_id"] == TEST_NODE_ID for e in events)

    async def test_with_battery_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with battery energy data fires event."""
        params = {
            DEVICE_TYPE_BATTERY_ENERGY: {
                "level": 75,
                "voltage": 3.8,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_battery_energy_update", lambda e: events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify battery update event was fired
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID

    async def test_with_gesture_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with IMU gesture data fires event."""
        params = {
            DEVICE_TYPE_IMU_GESTURE: {
                "gesture": "tap",
                "confidence": 85,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_imu_gesture_update", lambda e: events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify gesture update event was fired
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID

    async def test_with_input_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with interactive input data fires event."""
        params = {
            DEVICE_TYPE_INTERACTIVE_INPUT: {
                "event": "click",
                "value": 1,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_interactive_input_update", lambda e: events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify input update event was fired
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID

    async def test_with_sleep_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with low power sleep data fires event."""
        params = {
            DEVICE_TYPE_LOW_POWER_SLEEP: {
                "state": "awake",
                "wake_reason": "timer",
            }
        }
        events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_low_power_sleep_update", lambda e: events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify sleep update event was fired
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID

    async def test_binary_sensor_with_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test binary sensor dispatch fires event."""
        params = {
            DEVICE_TYPE_BINARY_SENSOR: {
                "state": True,
            }
        }
        events: list[Event] = []

        hass.bus.async_listen(
            "esp_weaver_binary_sensor_update", lambda e: events.append(e)
        )

        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        # Verify binary sensor update event was fired
        assert len(events) == 1
        assert events[0].data["node_id"] == TEST_NODE_ID
