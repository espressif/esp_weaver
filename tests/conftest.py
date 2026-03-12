# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for ESP-Weaver integration tests."""

from collections.abc import AsyncGenerator, Generator
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.iot.specs.events import DOMAIN
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_SECURITY_VERSION,
)


# Enable custom integrations for pytest-homeassistant-custom-component
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for all tests."""
    return


@pytest.fixture(autouse=True)
def mock_zeroconf(hass: HomeAssistant) -> Generator[None]:
    """Mock zeroconf to avoid socket creation in tests."""
    with patch("homeassistant.components.zeroconf.async_setup", return_value=True):
        yield


@pytest.fixture
def socket_enabled(socket_enabled: None) -> None:
    """Enable real sockets for tests that need network access.

    This fixture re-exports socket_enabled from pytest-homeassistant-custom-component.
    Tests that require real socket connections (e.g., network discovery tests)
    should request this fixture to bypass the default socket mocking.
    """
    return


# Test constants
TEST_NODE_ID = "test_node_123"
TEST_HOST = "192.168.1.100"
TEST_PORT = 8080
TEST_DEVICE_NAME = "Test ESP Device"
TEST_POP = "test_pop_value"


@pytest.fixture
def mock_config_entry_data() -> dict[str, Any]:
    """Return mock config entry data."""
    return {
        CONF_HOST: TEST_HOST,
        CONF_PORT: TEST_PORT,
        CONF_NODE_ID: TEST_NODE_ID,
        CONF_SECURITY_VERSION: 0,
    }


@pytest.fixture
def mock_config_entry_data_with_pop() -> dict[str, Any]:
    """Return mock config entry data with PoP."""
    return {
        CONF_HOST: TEST_HOST,
        CONF_PORT: TEST_PORT,
        CONF_NODE_ID: TEST_NODE_ID,
        CONF_SECURITY_VERSION: 1,
        CONF_CUSTOM_POP: TEST_POP,
    }


@pytest.fixture
def mock_esp_client() -> Generator[MagicMock]:
    """Create a mock ESP Local Control client."""
    with patch(
        "custom_components.esp_weaver.iot.client.client.ESPLocalCtrlClient"
    ) as mock_client_class:
        mock_client = MagicMock()
        mock_client.node_id = TEST_NODE_ID
        mock_client.ip = TEST_HOST
        mock_client.port = TEST_PORT
        mock_client.session_established = True
        mock_client.connect = AsyncMock(return_value=True)
        mock_client.disconnect = AsyncMock()
        mock_client.is_connected = AsyncMock(return_value=True)
        mock_client.get_property_values = AsyncMock(
            return_value=[
                {
                    "name": "config",
                    "type": 1,
                    "flags": 0,
                    "value": b'{"node_id":"test_node_123","devices":[]}',
                },
                {
                    "name": "params",
                    "type": 1,
                    "flags": 0,
                    "value": b"{}",
                },
            ]
        )
        mock_client.set_property_values = AsyncMock(return_value=True)
        mock_client.add_message_callback = MagicMock()
        mock_client.set_connection_error_callback = MagicMock()
        mock_client.mark_connection_error = MagicMock()
        mock_client_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_esp_api() -> Generator[MagicMock]:
    """Create a mock ESP HA API."""
    with patch(
        "custom_components.esp_weaver.iot.client.device_api.ESPWeaverApi"
    ) as mock_api_class:
        mock_api = MagicMock()
        mock_api.devices = {}
        mock_api.default_port = TEST_PORT
        mock_api.start_services = AsyncMock()
        mock_api.cleanup = AsyncMock()
        mock_api.register_device = AsyncMock(return_value=True)
        mock_api.unregister_device = AsyncMock()
        mock_api.register_config_entry = MagicMock()
        mock_api.is_device_available = MagicMock(return_value=True)
        mock_api.is_device_available_async = AsyncMock(return_value=True)
        mock_api.is_discovery_completed = MagicMock(return_value=False)
        mock_api.mark_discovery_completed = MagicMock()
        mock_api.is_mdns_available = AsyncMock(return_value=True)
        mock_api.parse_and_discover_entities = AsyncMock()
        mock_api.get_device_data = AsyncMock(return_value={})
        mock_api.registry = MagicMock()
        mock_api.registry.get_client = MagicMock(return_value=None)
        mock_api_class.return_value = mock_api
        yield mock_api


@pytest.fixture
def mock_security_manager() -> Generator[MagicMock]:
    """Create a mock security manager."""
    with patch(
        "custom_components.esp_weaver.config_flow.ESPSecurityManager"
    ) as mock_manager_class:
        mock_manager = MagicMock()
        mock_manager.clear_cache = MagicMock()
        mock_manager.detect_device_security = AsyncMock(
            return_value={CONF_SECURITY_VERSION: 0}
        )
        mock_manager.test_pop_connection = AsyncMock(return_value=True)
        mock_manager_class.return_value = mock_manager
        yield mock_manager


@pytest.fixture
def mock_discovered_devices() -> list[dict[str, Any]]:
    """Return mock discovered devices."""
    return [
        {
            "ip": TEST_HOST,
            CONF_NODE_ID: TEST_NODE_ID,
            CONF_PORT: TEST_PORT,
            "device_name": TEST_DEVICE_NAME,
        },
        {
            "ip": "192.168.1.101",
            CONF_NODE_ID: "test_node_456",
            CONF_PORT: 8080,
            "device_name": "ESP Device 2",
        },
    ]


@pytest.fixture
def mock_discover_devices(
    mock_discovered_devices: list[dict[str, Any]],
) -> Generator[AsyncMock]:
    """Mock the device discovery function."""
    with patch(
        "custom_components.esp_weaver.config_flow.async_discover_devices",
        new_callable=AsyncMock,
    ) as mock_discover:
        mock_discover.return_value = mock_discovered_devices
        yield mock_discover


@pytest.fixture
def mock_coordinator() -> Generator[MagicMock]:
    """Create a mock coordinator."""
    with patch(
        "custom_components.esp_weaver.coordinator.ESPDataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coord = MagicMock()
        mock_coord.node_id = TEST_NODE_ID
        mock_coord.device_name = TEST_DEVICE_NAME
        mock_coord.is_available = True
        mock_coord.discovery_completed = True
        mock_coord.last_update_success = True
        mock_coord.api = MagicMock()
        mock_coord.discovered_entities = {
            "sensors": {},
            "binary_sensors": {},
            "lights": {},
            "numbers": {},
        }
        mock_coord.entity_callbacks = {}
        mock_coord.async_config_entry_first_refresh = AsyncMock()
        mock_coord.async_refresh = AsyncMock()
        mock_coord.async_shutdown = AsyncMock()
        # async_add_listener is a sync method that returns a remove callback
        # Must return a callable (not AsyncMock) to avoid unawaited coroutine warnings
        mock_coord.async_add_listener = MagicMock(return_value=MagicMock())
        mock_coordinator_class.return_value = mock_coord
        yield mock_coord


@pytest.fixture
def mock_event_dispatcher() -> Generator[MagicMock]:
    """Create a mock event dispatcher."""
    with patch(
        "custom_components.esp_weaver.helpers.event_dispatcher.create_event_dispatcher"
    ) as mock_dispatcher_func:
        mock_dispatcher = MagicMock()
        mock_dispatcher.fire = MagicMock()
        mock_dispatcher.async_fire = AsyncMock()
        mock_dispatcher_func.return_value = mock_dispatcher
        yield mock_dispatcher


@pytest.fixture
async def mock_setup_entry(
    hass: HomeAssistant,
    mock_esp_api: MagicMock,
    mock_coordinator: MagicMock,
    mock_event_dispatcher: MagicMock,
) -> AsyncGenerator[None]:
    """Set up the integration for testing."""
    with (
        patch(
            "custom_components.esp_weaver._get_or_create_api",
            new_callable=AsyncMock,
            return_value=mock_esp_api,
        ),
        patch(
            "custom_components.esp_weaver.ESPDataUpdateCoordinator",
            return_value=mock_coordinator,
        ),
    ):
        yield


# Sensor test fixtures
@pytest.fixture
def mock_sensor_data() -> dict[str, Any]:
    """Return mock sensor discovery data."""
    return {
        CONF_NODE_ID: TEST_NODE_ID,
        "sensor_type": "temperature",
        "initial_value": 25.5,
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "device_info": {"name": TEST_DEVICE_NAME},
        "param": {},
    }


@pytest.fixture
def mock_light_data() -> dict[str, Any]:
    """Return mock light discovery data with full feature set."""
    return {
        CONF_NODE_ID: TEST_NODE_ID,
        "light_data": {
            "power": True,
            "brightness": 255,
            "hue": 0,
            "saturation": 100,
            "intensity": 25,  # Optional: only if device supports
            "light_mode": 0,  # Optional: only if device supports effect modes
        },
        "device_info": {"name": TEST_DEVICE_NAME},
    }


@pytest.fixture
def mock_binary_sensor_data() -> dict[str, Any]:
    """Return mock binary sensor discovery data."""
    return {
        CONF_NODE_ID: TEST_NODE_ID,
        "params": {
            "state": False,
            "device_class": "motion",
            "name": "Motion Sensor",
        },
        "device_info": {"name": TEST_DEVICE_NAME},
    }


@pytest.fixture
def mock_number_data() -> dict[str, Any]:
    """Return mock number (threshold) discovery data."""
    return {
        CONF_NODE_ID: TEST_NODE_ID,
        "sensor_type": "temperature",
        "device_name": TEST_DEVICE_NAME,
        "threshold_values": {
            "min": 10.0,
            "max": 30.0,
        },
    }


# Property response fixtures
@pytest.fixture
def mock_device_properties() -> list[dict[str, Any]]:
    """Return mock device property responses."""
    config_data = {
        "node_id": TEST_NODE_ID,
        "config_version": "1.0",
        "info": {
            "name": TEST_DEVICE_NAME,
            "fw_version": "1.0.0",
            "type": "ESP32",
        },
        "devices": [
            {
                "name": "Light",
                "type": "esp.device.lightbulb",
                "primary": "power",
                "params": [
                    {"name": "power", "type": "esp.param.power", "data_type": "bool"},
                    {
                        "name": "brightness",
                        "type": "esp.param.brightness",
                        "data_type": "int",
                        "bounds": {"min": 0, "max": 100},
                    },
                ],
            },
            {
                "name": "Temperature Sensor",
                "type": "esp.device.temperature-sensor",
                "primary": "temperature",
                "params": [
                    {
                        "name": "temperature",
                        "type": "esp.param.temperature",
                        "data_type": "float",
                    },
                ],
            },
        ],
    }

    params_data = {
        "Light": {
            "power": True,
            "brightness": 80,
        },
        "Temperature Sensor": {
            "temperature": 25.5,
        },
    }

    return [
        {
            "name": "config",
            "type": 1,
            "flags": 0,
            "value": json.dumps(config_data).encode(),
        },
        {
            "name": "params",
            "type": 1,
            "flags": 0,
            "value": json.dumps(params_data).encode(),
        },
    ]


# Common mock fixtures for test classes
@pytest.fixture
def mock_hass() -> MagicMock:
    """Create mock Home Assistant instance with common attributes.

    Note: hass.bus.async_fire is a sync method despite its name,
    so we use MagicMock instead of AsyncMock.
    """
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.config_entries = MagicMock()
    return hass


@pytest.fixture
def mock_registry() -> MagicMock:
    """Create mock device registry."""
    return MagicMock()


@pytest.fixture
def mock_device_entry() -> MagicMock:
    """Create a mock device entry for diagnostics tests."""
    device_entry = MagicMock()
    device_entry.name = TEST_DEVICE_NAME
    device_entry.model = f"ESP-{TEST_NODE_ID}"
    device_entry.manufacturer = "Espressif"
    device_entry.sw_version = "0.0.1"
    device_entry.hw_version = None
    device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}
    return device_entry


# Test helper functions
def create_mock_config_entry(
    data: dict[str, Any] | None = None,
    entry_id: str = "test_entry_id",
    title: str = TEST_DEVICE_NAME,
    unique_id: str = TEST_NODE_ID,
) -> MagicMock:
    """Create a mock config entry for testing.

    Args:
        data: Config entry data.
        entry_id: Entry ID.
        title: Entry title.
        unique_id: Unique ID.

    Returns:
        Mock config entry.
    """
    if data is None:
        data = {
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_NODE_ID: TEST_NODE_ID,
            CONF_SECURITY_VERSION: 0,
        }

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = entry_id
    mock_entry.domain = DOMAIN
    mock_entry.title = title
    mock_entry.unique_id = unique_id
    mock_entry.data = data
    mock_entry.options = {}
    mock_entry.version = 1
    mock_entry.minor_version = 1
    mock_entry.runtime_data = None
    mock_entry.async_on_unload = MagicMock()
    return mock_entry
