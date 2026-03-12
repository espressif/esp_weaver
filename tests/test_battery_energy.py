# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver battery energy sensor platform."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound

from custom_components.esp_weaver.battery_energy import (
    ESPWeaverBatterySensor,
    setup_discovery_listener,
)
from custom_components.esp_weaver.iot.entity_states import BatteryState
from custom_components.esp_weaver.iot.specs.battery_specs import (
    ALERT_LEVEL_CRITICAL,
    ALERT_LEVEL_LOW,
    ALERT_LEVEL_NORMAL,
    CHARGING_STATUS_CHARGING,
    CHARGING_STATUS_DISCHARGING,
)
from custom_components.esp_weaver.iot.specs.events import EVENT_BATTERY_ENERGY_UPDATE
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_ALERT_LEVEL,
    KEY_BATTERY_DATA,
    KEY_BATTERY_LEVEL,
    KEY_CHARGING_STATUS,
    KEY_TEMPERATURE,
    KEY_VOLTAGE,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


class TestBatterySensorInit:
    """Test battery sensor initialization."""

    def test_basic_initialization(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test basic initialization."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        assert sensor._attr_device_class == SensorDeviceClass.BATTERY
        assert sensor._attr_state_class == SensorStateClass.MEASUREMENT
        assert sensor._attr_native_unit_of_measurement == PERCENTAGE
        assert sensor._last_alert_level == ALERT_LEVEL_NORMAL
        assert isinstance(sensor._state, BatteryState)

    def test_initialization_with_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test initialization with initial data."""
        initial_data = {
            KEY_BATTERY_LEVEL: 75,
            KEY_VOLTAGE: 3.8,
            KEY_TEMPERATURE: 25.5,
            KEY_CHARGING_STATUS: CHARGING_STATUS_CHARGING,
            KEY_ALERT_LEVEL: ALERT_LEVEL_NORMAL,
        }

        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            initial_data=initial_data,
        )

        # Verify initial_data was applied to state
        assert sensor._attr_native_value == 75
        assert sensor._state.level == 75
        assert sensor._state.voltage == 3.8
        assert sensor._state.temperature == 25.5
        assert sensor._state.charging_status == CHARGING_STATUS_CHARGING
        assert sensor._state.alert_level == ALERT_LEVEL_NORMAL


class TestBatterySensorProperties:
    """Test battery sensor properties."""

    def test_icon_property_normal(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property for normal battery level (above medium threshold)."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        # Level must be > 50 (BATTERY_LEVEL_MEDIUM) to get battery-50 icon
        sensor._state.level = 60
        sensor._state.charging_status = CHARGING_STATUS_DISCHARGING
        sensor._state.alert_level = ALERT_LEVEL_NORMAL

        icon = sensor.icon
        assert icon == "mdi:battery-50"

    def test_icon_property_full(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property for full battery."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.level = 100
        sensor._state.charging_status = CHARGING_STATUS_DISCHARGING
        sensor._state.alert_level = ALERT_LEVEL_NORMAL

        icon = sensor.icon
        assert icon == "mdi:battery"

    def test_icon_property_low(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property for low battery (below 25%)."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.level = 10
        sensor._state.charging_status = CHARGING_STATUS_DISCHARGING
        sensor._state.alert_level = ALERT_LEVEL_NORMAL

        icon = sensor.icon
        assert icon == "mdi:battery-10"

    def test_icon_property_charging(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property when charging."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.level = 50
        sensor._state.charging_status = CHARGING_STATUS_CHARGING
        sensor._state.alert_level = ALERT_LEVEL_NORMAL

        icon = sensor.icon
        assert icon == "mdi:battery-charging"

    def test_icon_property_critical_alert(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test icon property with critical alert level."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.level = 5
        sensor._state.charging_status = CHARGING_STATUS_DISCHARGING
        sensor._state.alert_level = ALERT_LEVEL_CRITICAL

        icon = sensor.icon
        assert icon == "mdi:battery-alert"

    def test_extra_state_attributes(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test extra state attributes."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor._state.level = 80
        sensor._state.voltage = 4.1
        sensor._state.temperature = 28.5
        sensor._state.charging_status = CHARGING_STATUS_CHARGING
        sensor._state.alert_level = ALERT_LEVEL_NORMAL

        attrs = sensor.extra_state_attributes

        assert KEY_BATTERY_LEVEL in attrs
        assert attrs[KEY_BATTERY_LEVEL] == 80
        assert KEY_VOLTAGE in attrs
        assert attrs[KEY_VOLTAGE] == 4.1
        assert KEY_TEMPERATURE in attrs
        assert attrs[KEY_TEMPERATURE] == 28.5
        assert KEY_CHARGING_STATUS in attrs
        assert attrs[KEY_CHARGING_STATUS] == "Charging"  # Mapped display value
        assert KEY_ALERT_LEVEL in attrs
        assert attrs[KEY_ALERT_LEVEL] == "Normal"  # Mapped display value


class TestBatterySensorEventHandling:
    """Test battery sensor event handling."""

    def test_handle_battery_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling battery update event."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_BATTERY_ENERGY_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_BATTERY_DATA: {
                    "level": 60,
                    "voltage": 3.7,
                },
            },
        )

        sensor._handle_battery_update(event)

        sensor.async_write_ha_state.assert_called_once()

    def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test ignoring update for different node."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_BATTERY_ENERGY_UPDATE,
            data={
                CONF_NODE_ID: "other_node",
                KEY_BATTERY_DATA: {"level": 50},
            },
        )

        sensor._handle_battery_update(event)

        sensor.async_write_ha_state.assert_not_called()


class TestBatterySensorApplyUpdates:
    """Test battery sensor apply updates."""

    def test_apply_all_updates(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test applying all update fields."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )

        updates = {
            KEY_BATTERY_LEVEL: 45,
            KEY_VOLTAGE: 3.5,
            KEY_TEMPERATURE: 30.0,
            KEY_CHARGING_STATUS: "unknown",  # Edge case value not matching defined constants
            KEY_ALERT_LEVEL: ALERT_LEVEL_LOW,
        }

        sensor._apply_updates(updates)

        assert sensor._state.level == 45
        assert sensor._state.voltage == 3.5
        assert sensor._state.temperature == 30.0
        assert sensor._state.charging_status == "unknown"
        assert sensor._state.alert_level == ALERT_LEVEL_LOW


class TestBatterySensorAlertNotification:
    """Test battery sensor alert notifications."""

    def test_trigger_alert_notification_no_change(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test no notification when alert level unchanged."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor._state.alert_level = ALERT_LEVEL_NORMAL
        sensor._last_alert_level = ALERT_LEVEL_NORMAL

        # Spy on _send_alert_notification to verify it's not called
        sensor._send_alert_notification = MagicMock()
        sensor._trigger_alert_notification()

        # Verify notification was not sent when alert level unchanged
        sensor._send_alert_notification.assert_not_called()

    def test_trigger_alert_notification_with_change(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test notification when alert level changes."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor._state.alert_level = ALERT_LEVEL_LOW
        sensor._last_alert_level = ALERT_LEVEL_NORMAL

        with patch.object(sensor, "_send_alert_notification") as mock_send:
            sensor._trigger_alert_notification()
            mock_send.assert_called_once()

    def test_trigger_alert_notification_no_hass(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test trigger notification with no hass does not crash."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = None
        sensor._state.alert_level = ALERT_LEVEL_CRITICAL

        # Should not raise - hass None check in _trigger_alert_notification
        sensor._trigger_alert_notification()

    def test_send_alert_notification_normal(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test send notification clears when normal."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor._state.alert_level = ALERT_LEVEL_NORMAL

        # Mock async_create_task to consume the coroutine and return a mock task
        def consume_coro(coro):
            """Consume coroutine and return mock task."""
            coro.close()
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            return mock_task

        hass.async_create_task = MagicMock(side_effect=consume_coro)
        sensor._send_alert_notification(ALERT_LEVEL_NORMAL)

        # Verify dismiss tasks were scheduled (2 notifications: critical and low)
        assert hass.async_create_task.call_count == 2


class TestBatterySensorSafeNotificationCall:
    """Test safe notification call."""

    async def test_safe_notification_call_success(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test successful notification call."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            new_callable=AsyncMock,
        ) as mock_call:
            await sensor._safe_notification_call("create", {"title": "Test"})
            mock_call.assert_called_once()

    async def test_safe_notification_call_service_not_found(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test notification call when service not found."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            new_callable=AsyncMock,
            side_effect=ServiceNotFound("domain", "service"),
        ):
            # Should not raise
            await sensor._safe_notification_call("create", {"title": "Test"})

    async def test_safe_notification_call_ha_error(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test notification call with HomeAssistantError."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            new_callable=AsyncMock,
            side_effect=HomeAssistantError("Test error"),
        ):
            # Should not raise
            await sensor._safe_notification_call("create", {"title": "Test"})


class TestBatteryDiscoveryListener:
    """Test battery discovery listener setup."""

    def test_setup_discovery_listener(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test setting up discovery listener."""
        entry = create_mock_config_entry(mock_config_entry_data)

        with patch(
            "custom_components.esp_weaver.battery_energy.create_discovery_listener"
        ) as mock_create:
            setup_discovery_listener(hass, TEST_NODE_ID, entry)
            mock_create.assert_called_once()


class TestBatteryAsyncAddedToHass:
    """Test async_added_to_hass."""

    async def test_async_added_to_hass_registers_listener(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test that async_added_to_hass registers event listener."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass

        remove_callbacks = []
        sensor.async_on_remove = lambda cb: remove_callbacks.append(cb)

        await sensor.async_added_to_hass()

        # Should have registered 2 callbacks: coordinator listener and event bus listener
        assert len(remove_callbacks) == 2

    async def test_async_added_to_hass_sends_initial_alert(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test initial alert sent if battery critical at startup."""
        sensor = ESPWeaverBatterySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        sensor.hass = hass
        sensor._state.alert_level = ALERT_LEVEL_CRITICAL
        sensor.async_on_remove = MagicMock()

        with patch.object(sensor, "_send_alert_notification") as mock_send:
            await sensor.async_added_to_hass()
            mock_send.assert_called_once()
