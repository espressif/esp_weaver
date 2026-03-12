# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test error handling and edge cases across ESP-Weaver modules.

This module tests error handling, graceful degradation, and edge cases
for various components to ensure robustness of the integration.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import Event, HomeAssistant

from custom_components.esp_weaver import async_migrate_entry, async_setup_entry
from custom_components.esp_weaver.binary_sensor import (
    ESPWeaverBinarySensor,
    async_setup_entry as binary_sensor_setup_entry,
)
from custom_components.esp_weaver.diagnostics import _redact_ip_address
from custom_components.esp_weaver.helpers.sensor_alert import SensorAlertService
from custom_components.esp_weaver.iot.client.client import ESPLocalCtrlClient
from custom_components.esp_weaver.iot.client.device_api import ESPWeaverApi
from custom_components.esp_weaver.iot.client.security_validator import (
    ESPSecurityManager,
)
from custom_components.esp_weaver.iot.managers.device_registry import DeviceRegistry
from custom_components.esp_weaver.iot.managers.property_manager import (
    ConnectionCallbacks,
    PropertyManager,
)
from custom_components.esp_weaver.iot.parsers.config_parser import ESPDeviceParser
from custom_components.esp_weaver.iot.parsers.property_parser import (
    decode_property_value,
    parse_device_properties,
)
from custom_components.esp_weaver.iot.specs.events import (
    DOMAIN,
    EVENT_BINARY_SENSOR_UPDATE,
    EVENT_LIGHT_UPDATE,
)
from custom_components.esp_weaver.iot.specs.keys import CONF_NODE_ID, KEY_LIGHT_DATA
from custom_components.esp_weaver.iot.utils.binary_sensor_utils import (
    process_binary_sensor_update,
)
from custom_components.esp_weaver.iot.utils.sensor_utils import was_previously_violated
from custom_components.esp_weaver.light import (
    ESPWeaverLight,
    async_setup_entry as light_setup_entry,
)

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


# =============================================================================
# Config Parser Error Handling
# =============================================================================
class TestConfigParserRobustness:
    """Test config parser handles malformed data gracefully."""

    def test_empty_config_data_returns_empty_dict(self) -> None:
        """Test parser returns empty dict for empty input."""
        parser = ESPDeviceParser(DOMAIN)
        assert parser.parse_device_config("") == {}
        assert parser.parse_device_config(b"") == {}

    def test_malformed_utf8_returns_empty_dict(self) -> None:
        """Test parser returns empty dict for invalid UTF-8 sequences."""
        parser = ESPDeviceParser(DOMAIN)
        invalid_bytes = b'{"node_id": "test\xff"}'
        result = parser.parse_device_config(invalid_bytes)
        # Parser should return empty dict for malformed UTF-8 data
        assert isinstance(result, dict)
        assert result == {}

    def test_device_missing_type_field(self) -> None:
        """Test parser handles devices without type field."""
        parser = ESPDeviceParser(DOMAIN)
        config = json.dumps(
            {
                "node_id": "test",
                "info": {"name": "Test Device"},
                "devices": [{"name": "Light", "params": []}],
            }
        )
        result = parser.parse_device_config(config)
        assert isinstance(result, dict)
        assert "device_info" in result

    def test_unknown_device_type(self) -> None:
        """Test parser handles unknown device types by skipping them."""
        parser = ESPDeviceParser(DOMAIN)
        config = json.dumps(
            {"node_id": "test", "devices": [{"type": "unknown.type", "params": []}]}
        )
        result = parser.parse_device_config(config)
        assert isinstance(result, dict)
        # Unknown device types should be skipped, resulting in empty entities
        assert result.get("entities", []) == []
        assert result.get("platforms", {}) == {}


# =============================================================================
# Property Parser Edge Cases
# =============================================================================
class TestPropertyParserRobustness:
    """Test property parser handles edge cases."""

    def test_non_json_bytes_returns_none(self) -> None:
        """Test decoder returns None for non-JSON bytes."""
        result = decode_property_value(b"not json")
        assert result is None

    def test_config_property_skipped_during_parsing(self) -> None:
        """Test that config property is not included in results."""
        properties = [
            {"name": "config", "value": '{"node_id": "test"}'},
            {"name": "params", "value": '{"Light": {"power": true}}'},
        ]
        result = parse_device_properties(properties)
        # Verify params was processed
        assert result.raw_params is not None
        # Verify config was filtered but params data is present
        assert isinstance(result.raw_params, dict)
        assert "Light" in result.raw_params


# =============================================================================
# Binary Sensor Device Class Processing
# =============================================================================
class TestBinarySensorDeviceClassHandling:
    """Test binary sensor device class processing."""

    def test_bytes_device_class_decoded(self) -> None:
        """Test device class provided as bytes is decoded."""
        state_data = {
            "sensor_value": True,
            "params": {"device_class": b"motion"},
        }
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
        )
        assert result.has_changes is True
        assert result.device_class == "motion"

    def test_device_class_change_detected(self) -> None:
        """Test device class changes are detected."""
        # Use device classes that exist in BINARY_SENSOR_DEVICE_CLASS_MAP
        state_data = {"params": {"device_class": "motion"}}
        result = process_binary_sensor_update(
            state_data,
            current_state=False,
            current_device_class="door",
            default_device_class="door",
        )
        # Verify the change from "door" to "motion" was actually detected
        assert result.device_class == "motion"
        assert result.has_changes is True


# =============================================================================
# Security Manager Edge Cases
# =============================================================================
class TestSecurityManagerEdgeCases:
    """Test security manager handles various response formats."""

    async def test_protocol_security_detection(self) -> None:
        """Test ESP-IDF protocol security detection."""
        mock_local_ctrl = MagicMock()
        manager = ESPSecurityManager(mock_local_ctrl)

        mock_transport = MagicMock()
        mock_local_ctrl.get_transport = AsyncMock(return_value=mock_transport)
        # no_sec = True, no_pop = True (no security device)
        mock_local_ctrl.has_capability = AsyncMock(side_effect=[True, True])

        result = await manager.detect_device_security("192.168.1.100", 8080)
        assert result["security_version"] == 0
        assert result["pop_required"] is False

    async def test_security_version_1_device(self) -> None:
        """Test device with security version 1 (requires PoP)."""
        mock_local_ctrl = MagicMock()
        manager = ESPSecurityManager(mock_local_ctrl)

        mock_transport = MagicMock()
        mock_local_ctrl.get_transport = AsyncMock(return_value=mock_transport)
        # no_sec = False, no_pop = False
        mock_local_ctrl.has_capability = AsyncMock(side_effect=[False, False])

        result = await manager.detect_device_security("192.168.1.100", 8080)
        assert result["security_version"] == 1
        assert result["pop_required"] is True


# =============================================================================
# Property Manager Connection Handling
# =============================================================================
class TestPropertyManagerConnectionHandling:
    """Test property manager connection and retry logic."""

    async def test_set_property_triggers_connection(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test set_property establishes connection when needed."""
        registry = DeviceRegistry()
        manager = PropertyManager(hass, DOMAIN, registry)

        mock_establish = AsyncMock(return_value=(True, None))
        mock_reconnect = AsyncMock(return_value=True)
        manager.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=mock_establish,
                reconnect_and_retry=mock_reconnect,
            )
        )

        registry.register_device("test_node", "192.168.1.100", 8080)
        await manager.set_property("test_node", "power", True)
        mock_establish.assert_called()

    async def test_set_property_without_callbacks_fails(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test set_property fails gracefully without callbacks."""
        registry = DeviceRegistry()
        manager = PropertyManager(hass, DOMAIN, registry)
        registry.register_device("test_node", "192.168.1.100", 8080)

        result = await manager.set_property("test_node", "power", True)
        assert result is False


# =============================================================================
# Sensor Alert Service Error Handling
# =============================================================================
class TestSensorAlertServiceErrorHandling:
    """Test sensor alert service handles errors gracefully.

    Note: Some tests call private methods (_send_alert_to_device, etc.)
    intentionally to test internal error handling paths in isolation.
    """

    async def test_handles_key_error_in_violations(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test graceful handling of KeyError in violation check."""
        service = SensorAlertService(hass, DOMAIN)
        with patch.object(
            service, "_get_threshold_values", side_effect=KeyError("test")
        ):
            # Should not raise
            await service.check_and_handle_violations(
                node_id="test",
                device_name="Test Device",
                sensor_type="temperature",
                current_value=25.0,
                old_value=24.0,
            )


# =============================================================================
# Diagnostics Privacy Protection
# =============================================================================
class TestDiagnosticsPrivacyProtection:
    """Test diagnostics redacts sensitive information.

    Note: ESP local ctrl only supports IPv4, so IPv6 tests are not included.
    """

    def test_empty_ip_redacted(self) -> None:
        """Test empty IP address is redacted."""
        result = _redact_ip_address("")
        assert result == "**REDACTED**"

    def test_malformed_ipv4_redacted(self) -> None:
        """Test malformed IPv4 address is redacted."""
        result = _redact_ip_address("192.168.1")
        assert result == "**REDACTED**"

    def test_hostname_redacted(self) -> None:
        """Test hostname is redacted."""
        result = _redact_ip_address("localhost")
        assert result == "**REDACTED**"


# =============================================================================
# Device API Initialization
# =============================================================================
class TestDeviceApiInitialization:
    """Test device API initialization edge cases."""

    async def test_start_services_handles_exception(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test start_services handles exceptions gracefully."""
        api = ESPWeaverApi(hass, DOMAIN)
        with patch(
            "custom_components.esp_weaver.iot.client.device_api.zeroconf.async_get_instance",
            side_effect=RuntimeError("test error"),
        ):
            # Should not raise
            await api.start_services()
        assert api._global_browser is None


# =============================================================================
# ESP Local Control Client Edge Cases
# =============================================================================
class TestESPLocalCtrlClientEdgeCases:
    """Test ESP Local Control client edge cases."""

    async def test_get_properties_without_listener_returns_empty(self) -> None:
        """Test get_property_values returns empty without listener."""
        client = ESPLocalCtrlClient(node_id="test", ip="192.168.1.100")
        client.transport = MagicMock()
        client.session_established = True
        client._http_listener = None

        result = await client._get_all_property_values_via_listener()
        assert result == []

    async def test_set_properties_with_connection_error_flag(self) -> None:
        """Test set_property_values fails when connection_error is set."""
        client = ESPLocalCtrlClient(node_id="test", ip="192.168.1.100")
        client.transport = MagicMock()
        client.session_established = True
        client._connection_error = True

        result = await client.set_property_values([1], [{"power": True}])
        assert result is False

    async def test_set_properties_without_transport_fails(self) -> None:
        """Test set_property_values fails without transport."""
        client = ESPLocalCtrlClient(node_id="test", ip="192.168.1.100")
        client.transport = None
        client.session_established = False

        result = await client.set_property_values([1], [{"power": True}])
        assert result is False

    async def test_set_properties_handles_os_error(self) -> None:
        """Test set_property_values handles OSError and sets error flag."""
        client = ESPLocalCtrlClient(node_id="test", ip="192.168.1.100")
        client.transport = MagicMock()
        client.session_established = True
        client.security_ctx = MagicMock()
        client._connection_error = False
        client._http_listener = MagicMock()

        with patch(
            "custom_components.esp_weaver.iot.client.client.local_ctrl"
        ) as mock_esp:
            mock_esp.set_property_values = AsyncMock(side_effect=OSError("test"))
            result = await client.set_property_values([1], [{"power": True}])

        assert result is False
        assert client._connection_error is True


# =============================================================================
# Integration Setup Edge Cases
# =============================================================================
class TestIntegrationSetupEdgeCases:
    """Test integration setup edge cases."""

    async def test_migrate_entry_rejects_future_version(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test migration fails for unsupported future versions."""
        mock_entry = MagicMock()
        mock_entry.version = 999
        mock_entry.minor_version = 0

        result = await async_migrate_entry(hass, mock_entry)
        assert result is False

    async def test_setup_entry_requires_node_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test setup fails without node_id."""
        mock_entry = MagicMock()
        mock_entry.data = {}

        result = await async_setup_entry(hass, mock_entry)
        assert result is False

    async def test_setup_entry_requires_host(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test setup fails without host."""
        mock_entry = MagicMock()
        mock_entry.data = {"node_id": "test"}

        result = await async_setup_entry(hass, mock_entry)
        assert result is False


# =============================================================================
# Platform Setup Edge Cases
# =============================================================================
class TestPlatformSetupEdgeCases:
    """Test platform setup edge cases."""

    async def test_binary_sensor_setup_without_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test binary sensor setup returns early without coordinator."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None
        async_add_entities = MagicMock()

        await binary_sensor_setup_entry(hass, entry, async_add_entities)
        async_add_entities.assert_not_called()

    async def test_light_setup_without_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test light setup returns early without coordinator."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = None
        async_add_entities = MagicMock()

        await light_setup_entry(hass, entry, async_add_entities)
        async_add_entities.assert_not_called()


# =============================================================================
# Entity Update Handling
# =============================================================================
class TestEntityUpdateHandling:
    """Test entity update event handling."""

    async def test_binary_sensor_state_change_updates_ha(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test binary sensor state change triggers HA state update."""
        sensor = ESPWeaverBinarySensor(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            sensor_params={},
        )
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()
        sensor._attr_is_on = False

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

    async def test_light_update_with_empty_data_ignored(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light update with empty data is ignored."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={},
        )
        light.hass = hass
        light.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LIGHT_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_LIGHT_DATA: {},
            },
        )
        light._handle_light_update(event)
        light.async_write_ha_state.assert_not_called()


# =============================================================================
# Sensor Threshold Utilities
# =============================================================================
class TestSensorThresholdViolationDetection:
    """Test sensor threshold violation detection."""

    def test_high_threshold_violation_detected(self) -> None:
        """Test previous high threshold violation is detected."""
        result = was_previously_violated(
            old_value=35.0,
            min_threshold=10.0,
            max_threshold=30.0,
        )
        assert result is True

    def test_low_threshold_violation_detected(self) -> None:
        """Test previous low threshold violation is detected."""
        result = was_previously_violated(
            old_value=5.0,
            min_threshold=10.0,
            max_threshold=30.0,
        )
        assert result is True

    def test_normal_value_not_flagged(self) -> None:
        """Test normal values are not flagged as violations."""
        result = was_previously_violated(
            old_value=20.0,
            min_threshold=10.0,
            max_threshold=30.0,
        )
        assert result is False
