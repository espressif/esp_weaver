# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver managers module."""

from unittest.mock import MagicMock

import pytest

from custom_components.esp_weaver.iot.managers.device_registry import (
    DeviceInfo,
    DeviceRegistry,
)


@pytest.fixture
def registry() -> DeviceRegistry:
    """Create a fresh DeviceRegistry for each test."""
    return DeviceRegistry()


class TestDeviceInfo:
    """Test DeviceInfo dataclass."""

    def test_basic_creation(self) -> None:
        """Test basic DeviceInfo creation."""
        info = DeviceInfo(node_id="test_node", ip="192.168.1.100")

        assert info.node_id == "test_node"
        assert info.ip == "192.168.1.100"
        assert info.port == 8080  # Default port
        assert info.registered is False
        assert info.device_info == {}
        assert info.parsed_config == {}
        assert info.current_values == {}
        assert info.properties == {}
        assert info.last_success is None

    def test_creation_with_all_fields(self) -> None:
        """Test DeviceInfo with all fields."""
        info = DeviceInfo(
            node_id="test_node",
            ip="192.168.1.100",
            port=9090,
            registered=True,
            device_info={"model": "ESP32"},
            parsed_config={"key": "value"},
            current_values={"temp": 25},
            properties={"power": True},
            last_success=1234567890.0,
        )

        assert info.node_id == "test_node"
        assert info.ip == "192.168.1.100"
        assert info.port == 9090
        assert info.registered is True
        assert info.device_info["model"] == "ESP32"
        assert info.parsed_config["key"] == "value"
        assert info.current_values["temp"] == 25
        assert info.properties["power"] is True
        assert info.last_success == 1234567890.0

    def test_to_dict(self) -> None:
        """Test converting DeviceInfo to dictionary."""
        info = DeviceInfo(
            node_id="test_node",
            ip="192.168.1.100",
            port=8080,
            registered=True,
            device_info={"model": "ESP32"},
            parsed_config={"key": "value"},
            current_values={"temp": 25},
            properties={"power": True},
            last_success=1234567890.0,
        )

        result = info.to_dict()

        # Verify all fields are included in the dictionary
        assert result["node_id"] == "test_node"
        assert result["ip"] == "192.168.1.100"
        assert result["port"] == 8080
        assert result["registered"] is True
        assert result["device_info"] == {"model": "ESP32"}
        assert result["parsed_config"] == {"key": "value"}
        assert result["current_values"] == {"temp": 25}
        assert result["properties"] == {"power": True}
        assert result["last_success"] == 1234567890.0


class TestDeviceRegistry:
    """Test DeviceRegistry class."""

    def test_initialization(self, registry: DeviceRegistry) -> None:
        """Test registry initialization."""
        assert registry.default_port == 8080
        assert registry.get_all_devices() == {}
        # Verify no clients exist via public API
        assert registry.get_client("any_node") is None

    def test_initialization_custom_port(self) -> None:
        """Test registry with custom port."""
        registry = DeviceRegistry(default_port=9000)

        assert registry.default_port == 9000

    def test_register_new_device(self) -> None:
        """Test registering a new device."""
        registry = DeviceRegistry()

        device = registry.register_device(
            node_id="test_node",
            ip="192.168.1.100",
        )

        assert device.node_id == "test_node"
        assert device.ip == "192.168.1.100"
        assert device.port == 8080
        assert registry.get_device("test_node") is not None

    def test_register_device_custom_port(self) -> None:
        """Test registering device with custom port."""
        registry = DeviceRegistry()

        device = registry.register_device(
            node_id="test_node",
            ip="192.168.1.100",
            port=9090,
        )

        assert device.port == 9090

    def test_update_existing_device(self) -> None:
        """Test updating existing device."""
        registry = DeviceRegistry()

        # Register first
        original_device = registry.register_device(
            node_id="test_node", ip="192.168.1.100"
        )

        # Update
        updated_device = registry.register_device(
            node_id="test_node",
            ip="192.168.1.200",
            port=9000,
        )

        assert updated_device.ip == "192.168.1.200"
        assert updated_device.port == 9000
        # Should still be the same device object (in-place update)
        assert len(registry.get_all_devices()) == 1
        assert original_device is updated_device

    def test_get_device(self) -> None:
        """Test getting device by node_id."""
        registry = DeviceRegistry()

        registry.register_device(node_id="test_node", ip="192.168.1.100")

        device = registry.get_device("test_node")
        assert device is not None
        assert device.node_id == "test_node"

    def test_get_device_not_found(self) -> None:
        """Test getting non-existent device."""
        registry = DeviceRegistry()

        device = registry.get_device("non_existent")
        assert device is None

    def test_get_all_devices(self) -> None:
        """Test getting all devices."""
        registry = DeviceRegistry()

        registry.register_device(node_id="node1", ip="192.168.1.100")
        registry.register_device(node_id="node2", ip="192.168.1.101")

        devices = registry.get_all_devices()

        assert len(devices) == 2
        assert "node1" in devices
        assert "node2" in devices
        # Verify it's a copy by modifying and checking the registry state
        devices["node3"] = None
        assert "node3" not in registry.get_all_devices()


class TestDeviceRegistryClients:
    """Test DeviceRegistry client management."""

    def test_set_client(self) -> None:
        """Test setting client for device."""
        registry = DeviceRegistry()
        mock_client = MagicMock()

        registry.set_client("test_node", mock_client)

        retrieved_client = registry.get_client("test_node")
        assert retrieved_client is not None
        assert retrieved_client == mock_client

    def test_get_client(self) -> None:
        """Test getting client for device."""
        registry = DeviceRegistry()
        mock_client = MagicMock()
        registry.set_client("test_node", mock_client)

        client = registry.get_client("test_node")

        assert client == mock_client

    def test_get_client_not_found(self) -> None:
        """Test getting non-existent client."""
        registry = DeviceRegistry()

        client = registry.get_client("non_existent")

        assert client is None

    def test_remove_client(self) -> None:
        """Test removing client."""
        registry = DeviceRegistry()
        mock_client = MagicMock()
        registry.set_client("test_node", mock_client)

        registry.remove_client("test_node")

        assert registry.get_client("test_node") is None

    def test_remove_non_existent_client(self) -> None:
        """Test removing non-existent client."""
        registry = DeviceRegistry()

        # Should not raise
        registry.remove_client("non_existent")


class TestDeviceRegistryLocks:
    """Test DeviceRegistry lock management."""

    def test_get_lock_returns_single_instance(self, registry: DeviceRegistry) -> None:
        """Test lock creation and idempotent retrieval."""
        lock1 = registry.get_lock("test_node")
        lock2 = registry.get_lock("test_node")

        assert lock1 is not None
        assert lock1 is lock2

    def test_device_lock(self, registry: DeviceRegistry) -> None:
        """Test device_lock method."""
        lock = registry.device_lock("test_node")

        assert lock is not None
        # Verify it returns the same lock as get_lock
        assert lock is registry.get_lock("test_node")


class TestDeviceRegistryDiscovery:
    """Test DeviceRegistry discovery tracking."""

    def test_mark_discovery_completed(self) -> None:
        """Test marking discovery as completed."""
        registry = DeviceRegistry()

        registry.mark_discovery_completed("test_node")

        assert registry.is_discovery_completed("test_node")

    def test_is_discovery_completed_false(self) -> None:
        """Test discovery not completed."""
        registry = DeviceRegistry()

        assert not registry.is_discovery_completed("test_node")

    def test_add_discovered_platform_new(self) -> None:
        """Test adding new platform."""
        registry = DeviceRegistry()

        result = registry.add_discovered_platform("light")

        assert result is True
        # Verify by trying to add again (should return False if already added)
        assert registry.add_discovered_platform("light") is False

    def test_add_discovered_platform_existing(self) -> None:
        """Test adding existing platform."""
        registry = DeviceRegistry()
        registry.add_discovered_platform("light")

        result = registry.add_discovered_platform("light")

        assert result is False


class TestDeviceRegistryConfigEntry:
    """Test DeviceRegistry config entry management."""

    def test_config_entry_registration_and_retrieval(
        self, registry: DeviceRegistry
    ) -> None:
        """Test registering and retrieving config entry ID."""
        registry.register_config_entry("test_node", "config_entry_123")

        entry_id = registry.get_config_entry_id("test_node")

        assert entry_id == "config_entry_123"

    def test_get_config_entry_id_not_found(self, registry: DeviceRegistry) -> None:
        """Test getting non-existent config entry."""
        entry_id = registry.get_config_entry_id("non_existent")

        assert entry_id is None
