# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver threshold manager module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.helpers.threshold_manager import ThresholdManager
from custom_components.esp_weaver.iot.specs.events import (
    EVENT_SENSOR_DISCOVERED,
    EVENT_THRESHOLD_DATA_RECEIVED,
)
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_DEVICE_INFO,
    KEY_NAME,
    KEY_PARAM_NAME,
    KEY_RAW_TYPE,
    KEY_SENSOR_TYPE,
    KEY_SOURCE,
    KEY_VALUE,
    SOURCE_NUMBER_ENTITY,
)
from custom_components.esp_weaver.iot.specs.sensor_specs import THRESHOLD_SENSOR_TYPES

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID


class TestThresholdManagerInit:
    """Test ThresholdManager initialization."""

    async def test_initialization(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test basic initialization."""
        manager = ThresholdManager(hass, "esp_weaver")

        assert manager.hass == hass
        assert manager.domain == "esp_weaver"


class TestThresholdManagerEventHandlers:
    """Test ThresholdManager event handlers."""

    async def test_report_handler_ignores_other_node(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test report handler ignores other node."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_report_handler(TEST_NODE_ID)

        # Track fired events (handler fires EVENT_SENSOR_DISCOVERED)
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        # Create event for different node
        event = MagicMock()
        event.data = {CONF_NODE_ID: "other_node"}

        await handler(event)
        await hass.async_block_till_done()

        # Verify no events were fired for non-matching node
        assert len(events_fired) == 0

    async def test_report_handler_processes_matching_node(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test report handler processes matching node."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_report_handler(TEST_NODE_ID)

        # Track fired events (handler fires EVENT_SENSOR_DISCOVERED)
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        # Create event for matching node with threshold data
        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            "threshold_data": {"temperature_min_threshold": 10},
        }

        await handler(event)
        await hass.async_block_till_done()

        # Verify events were fired for matching node
        assert len(events_fired) == 1
        assert events_fired[0].data.get(CONF_NODE_ID) == TEST_NODE_ID

    async def test_update_handler_is_async(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test update handler is async callable."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_update_handler(TEST_NODE_ID)

        # Should be a coroutine function
        assert asyncio.iscoroutinefunction(handler)


class TestThresholdManagerListeners:
    """Test ThresholdManager listener setup."""

    async def test_setup_listeners(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test setting up listeners."""
        manager = ThresholdManager(hass, "esp_weaver")

        unsub_list = manager.setup_listeners(TEST_NODE_ID)

        # Should return list of unsubscribe callbacks
        assert isinstance(unsub_list, list)
        assert len(unsub_list) > 0
        # Verify all items are callable
        for unsub in unsub_list:
            assert callable(unsub)


class TestThresholdManagerDiscovery:
    """Test ThresholdManager discovery helpers."""

    async def test_replay_discovered_sensors(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test replaying discovered sensors."""
        manager = ThresholdManager(hass, "esp_weaver")

        # Track fired events
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        # Create a mock sensor info function
        get_sensor_info = MagicMock(
            return_value={
                KEY_SENSOR_TYPE: "temperature",
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_name": "Temperature Sensor",
            }
        )

        mock_coordinator = MagicMock()
        mock_coordinator.discovered_entities = {
            "sensors": {
                f"{TEST_NODE_ID}_temperature": {"entity": MagicMock()},
            }
        }

        manager.replay_discovered_sensors(
            TEST_NODE_ID,
            get_sensor_info,
            mock_coordinator,
        )
        await hass.async_block_till_done()

        # Verify get_sensor_info was called
        get_sensor_info.assert_called_once()
        # Verify event was fired for threshold sensor
        assert len(events_fired) == 1
        assert events_fired[0].data.get(CONF_NODE_ID) == TEST_NODE_ID
        assert events_fired[0].data.get(KEY_SENSOR_TYPE) == "temperature"

    async def test_replay_no_coordinator(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test replay with no coordinator discovered_entities."""
        manager = ThresholdManager(hass, "esp_weaver")

        # Track fired events to verify early exit
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        mock_coordinator = MagicMock()
        mock_coordinator.discovered_entities = {}

        get_sensor_info = MagicMock()
        manager.replay_discovered_sensors(
            TEST_NODE_ID,
            get_sensor_info,
            mock_coordinator,
        )
        await hass.async_block_till_done()

        # Verify early exit - no events fired and get_sensor_info not called
        assert len(events_fired) == 0
        get_sensor_info.assert_not_called()

    async def test_replay_empty_sensors(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test replay with empty sensors dict."""
        manager = ThresholdManager(hass, "esp_weaver")

        # Track fired events to verify no processing
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        mock_coordinator = MagicMock()
        mock_coordinator.discovered_entities = {"sensors": {}}

        manager.replay_discovered_sensors(
            TEST_NODE_ID,
            MagicMock(return_value=None),
            mock_coordinator,
        )
        await hass.async_block_till_done()

        # Verify no events fired for empty sensors
        assert len(events_fired) == 0

    async def test_replay_skips_non_matching_node(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test replay skips entities for other nodes."""
        manager = ThresholdManager(hass, "esp_weaver")

        # Track fired events to verify skipping
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        def get_sensor_info(entity):
            return {
                KEY_SENSOR_TYPE: "temperature",
                CONF_NODE_ID: "other_node",
                "sensor_name": "Temperature Sensor",
            }

        mock_coordinator = MagicMock()
        mock_coordinator.discovered_entities = {
            "sensors": {
                "other_node_temperature": {"entity": MagicMock()},
            }
        }

        manager.replay_discovered_sensors(
            TEST_NODE_ID,
            get_sensor_info,
            mock_coordinator,
        )
        await hass.async_block_till_done()

        # Verify no events fired for non-matching node
        assert len(events_fired) == 0

    async def test_replay_skips_non_threshold_types(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test replay skips non-threshold sensor types."""
        manager = ThresholdManager(hass, "esp_weaver")

        # Track fired events to verify skipping
        events_fired: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: events_fired.append(e),
        )

        def get_sensor_info(entity):
            return {
                KEY_SENSOR_TYPE: "motion",  # Not a threshold type
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_name": "Motion Sensor",
            }

        mock_coordinator = MagicMock()
        mock_coordinator.discovered_entities = {
            "sensors": {
                f"{TEST_NODE_ID}_motion": {"entity": MagicMock()},
            }
        }

        # Verify motion is not in threshold types
        assert "motion" not in THRESHOLD_SENSOR_TYPES

        manager.replay_discovered_sensors(
            TEST_NODE_ID,
            get_sensor_info,
            mock_coordinator,
        )
        await hass.async_block_till_done()

        # Verify no events fired for non-threshold type
        assert len(events_fired) == 0

    async def test_replay_fires_event_for_threshold_sensor(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test replay fires event for valid threshold sensor."""
        manager = ThresholdManager(hass, "esp_weaver")

        # Capture fired events
        fired_events: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: fired_events.append(e),
        )

        def get_sensor_info(entity):
            return {
                KEY_SENSOR_TYPE: "temperature",
                CONF_NODE_ID: TEST_NODE_ID,
                "sensor_name": "Temperature Sensor",
            }

        mock_coordinator = MagicMock()
        mock_coordinator.discovered_entities = {
            "sensors": {
                f"{TEST_NODE_ID}_temperature": {"entity": MagicMock()},
            }
        }

        manager.replay_discovered_sensors(
            TEST_NODE_ID,
            get_sensor_info,
            mock_coordinator,
        )
        await hass.async_block_till_done()

        # Verify event was fired for threshold sensor
        assert len(fired_events) > 0


class TestThresholdManagerExtractData:
    """Test ThresholdManager data extraction."""

    async def test_extract_discovery_event_data_valid(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test extracting discovery event data."""
        manager = ThresholdManager(hass, "esp_weaver")

        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "temperature",
            KEY_DEVICE_INFO: {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_NAME: TEST_DEVICE_NAME,
            },
        }

        result = manager.extract_discovery_event_data(event_data)

        # Should return tuple of (node_id, sensor_type, device_name)
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert result[0] == TEST_NODE_ID
        assert result[1] == "temperature"
        assert result[2] == TEST_DEVICE_NAME

    async def test_extract_discovery_event_data_missing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test extracting with missing data."""
        manager = ThresholdManager(hass, "esp_weaver")

        event_data = {}

        result = manager.extract_discovery_event_data(event_data)

        # Should return tuple with None values
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None

    async def test_extract_discovery_event_data_with_device_info(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test extracting from device_info."""
        manager = ThresholdManager(hass, "esp_weaver")

        event_data = {
            KEY_DEVICE_INFO: {
                CONF_NODE_ID: TEST_NODE_ID,
                KEY_NAME: TEST_DEVICE_NAME,
            },
            KEY_SENSOR_TYPE: "humidity",
        }

        result = manager.extract_discovery_event_data(event_data)

        assert result[0] == TEST_NODE_ID
        assert result[1] == "humidity"
        assert result[2] == TEST_DEVICE_NAME

    async def test_extract_discovery_event_data_with_raw_type(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test extracting with raw_type key."""
        manager = ThresholdManager(hass, "esp_weaver")

        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_RAW_TYPE: "temperature",
        }

        result = manager.extract_discovery_event_data(event_data)

        assert result[0] == TEST_NODE_ID
        assert result[1] == "temperature"
        # device_name uses default format when not in device_info
        assert result[2] == f"ESP-{TEST_NODE_ID}"

    async def test_extract_discovery_event_data_threshold_type_skipped(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test extracting with threshold param name is skipped."""
        manager = ThresholdManager(hass, "esp_weaver")

        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "temperature_min_threshold",
        }

        result = manager.extract_discovery_event_data(event_data)

        # Should return None for sensor_type since it's a threshold param
        assert result[0] == TEST_NODE_ID
        assert result[1] is None

    async def test_extract_discovery_event_data_non_threshold_sensor(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test extracting with non-threshold sensor type."""
        manager = ThresholdManager(hass, "esp_weaver")

        event_data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SENSOR_TYPE: "motion",
        }

        result = manager.extract_discovery_event_data(event_data)

        # Should return None for sensor_type since motion is not in THRESHOLD_SENSOR_TYPES
        assert result[0] == TEST_NODE_ID
        assert result[1] is None
        assert result[2] is None  # device_name is absent


class TestThresholdManagerUpdateHandler:
    """Test ThresholdManager update handler."""

    async def test_update_handler_ignores_other_node(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test update handler ignores events for other nodes."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_update_handler(TEST_NODE_ID)

        # Setup mock API to verify it's not called
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock()
        hass.data["esp_weaver"] = {"api": mock_api}

        event = MagicMock()
        event.data = {CONF_NODE_ID: "other_node"}

        await handler(event)

        # Verify API was not called for non-matching node
        mock_api.set_local_ctrl_property.assert_not_called()

    async def test_update_handler_ignores_non_number_source(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test update handler ignores non-number entity sources."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_update_handler(TEST_NODE_ID)

        # Setup mock API to verify it's not called
        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock()
        hass.data["esp_weaver"] = {"api": mock_api}

        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SOURCE: "other_source",
        }

        await handler(event)

        # Verify API was not called for non-number source
        mock_api.set_local_ctrl_property.assert_not_called()

    async def test_update_handler_no_api(
        self,
        hass: HomeAssistant,
        caplog,
    ) -> None:
        """Test update handler when API not found."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_update_handler(TEST_NODE_ID)

        hass.data.clear()

        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SOURCE: SOURCE_NUMBER_ENTITY,
        }

        result = await handler(event)

        # Verify handler returned early (returns None)
        assert result is None
        # Verify hass.data is empty (no side effects)
        assert len(hass.data) == 0
        # Verify warning was logged about missing API
        assert any("API" in msg or "api" in msg.lower() for msg in caplog.messages)

    async def test_update_handler_success(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test update handler success."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_update_handler(TEST_NODE_ID)

        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(return_value=True)
        hass.data["esp_weaver"] = {"api": mock_api}

        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SOURCE: SOURCE_NUMBER_ENTITY,
            KEY_PARAM_NAME: "temperature_min_threshold",
            KEY_VALUE: 15.0,
            KEY_SENSOR_TYPE: "temperature",
            "threshold_type": "min",
        }

        await handler(event)

        mock_api.set_local_ctrl_property.assert_called_once_with(
            TEST_NODE_ID,
            "temperature_min_threshold",
            15.0,
        )

    async def test_update_handler_api_failure(
        self,
        hass: HomeAssistant,
        caplog,
    ) -> None:
        """Test update handler when API call fails."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_update_handler(TEST_NODE_ID)

        mock_api = MagicMock()
        mock_api.set_local_ctrl_property = AsyncMock(return_value=False)
        hass.data["esp_weaver"] = {"api": mock_api}

        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            KEY_SOURCE: SOURCE_NUMBER_ENTITY,
            KEY_PARAM_NAME: "temperature_max_threshold",
            KEY_VALUE: 30.0,
            KEY_SENSOR_TYPE: "temperature",
            "threshold_type": "max",
        }

        # Should not raise but should log error
        await handler(event)

        # Verify error was logged about failed threshold update
        error_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "ERROR"
        ]
        assert any("failed" in msg.lower() for msg in error_messages), (
            f"Expected error message about failure, got: {error_messages}"
        )


class TestThresholdManagerReportHandler:
    """Test ThresholdManager report handler."""

    async def test_report_handler_with_threshold_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test report handler processes threshold data."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_report_handler(TEST_NODE_ID)

        # Track fired events
        sensor_events: list = []
        threshold_events: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: sensor_events.append(e),
        )
        hass.bus.async_listen(
            EVENT_THRESHOLD_DATA_RECEIVED,
            lambda e: threshold_events.append(e),
        )

        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            "threshold_data": {
                "temperature_min_threshold": 10.0,
                "temperature_max_threshold": 30.0,
            },
        }

        await handler(event)
        await hass.async_block_till_done()

        # Verify events were fired for threshold data
        # At least one sensor discovery and one threshold data event
        assert len(sensor_events) >= 1
        assert len(threshold_events) >= 1

        # Validate sensor event payload
        sensor_event = next(
            (e for e in sensor_events if e.data.get(CONF_NODE_ID) == TEST_NODE_ID),
            None,
        )
        assert sensor_event is not None, "Expected sensor event for TEST_NODE_ID"

        # Validate threshold event payload contains expected threshold data
        threshold_event = next(
            (e for e in threshold_events if e.data.get(CONF_NODE_ID) == TEST_NODE_ID),
            None,
        )
        assert threshold_event is not None, "Expected threshold event for TEST_NODE_ID"
        threshold_data = threshold_event.data.get("threshold_data", {})
        assert threshold_data.get("temperature_min_threshold") == 10.0
        assert threshold_data.get("temperature_max_threshold") == 30.0

    async def test_report_handler_empty_threshold_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test report handler with empty threshold data."""
        manager = ThresholdManager(hass, "esp_weaver")
        handler = manager.create_report_handler(TEST_NODE_ID)

        # Track fired events
        sensor_events: list = []
        hass.bus.async_listen(
            EVENT_SENSOR_DISCOVERED,
            lambda e: sensor_events.append(e),
        )

        event = MagicMock()
        event.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            "threshold_data": {},
        }

        await handler(event)
        await hass.async_block_till_done()

        # No events should be fired for empty threshold data
        assert len(sensor_events) == 0
