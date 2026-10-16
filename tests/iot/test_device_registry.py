# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the registry that tracks what the integration knows about each node."""

from unittest.mock import MagicMock, patch

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
        assert info.firmware_version is None
        assert info.last_success is None
        assert info.raw_config == {}
        assert info.raw_params == {}

    def test_creation_with_all_fields(self) -> None:
        """Test DeviceInfo with all fields."""
        info = DeviceInfo(
            node_id="test_node",
            ip="192.168.1.100",
            port=9090,
            registered=True,
            firmware_version="1.2.3",
            last_success=1234567890.0,
            raw_config={"devices": []},
            raw_params={"Light": {"Power": True}},
        )

        assert info.node_id == "test_node"
        assert info.ip == "192.168.1.100"
        assert info.port == 9090
        assert info.registered is True
        assert info.firmware_version == "1.2.3"
        assert info.last_success == 1234567890.0
        assert info.raw_config == {"devices": []}
        assert info.raw_params == {"Light": {"Power": True}}

    def test_to_dict(self) -> None:
        """Test converting DeviceInfo to dictionary."""
        info = DeviceInfo(
            node_id="test_node",
            ip="192.168.1.100",
            port=8080,
            registered=True,
            firmware_version="1.2.3",
            last_success=1234567890.0,
            raw_config={"devices": []},
            raw_params={"Light": {"Power": True}},
        )

        result = info.to_dict()

        # The raw report and the resolved bindings stay out of the dictionary;
        # diagnostics reads them from the device object instead.
        assert set(result) == {
            "node_id",
            "ip",
            "port",
            "registered",
            "firmware_version",
            "last_success",
        }
        assert result["node_id"] == "test_node"
        assert result["ip"] == "192.168.1.100"
        assert result["port"] == 8080
        assert result["registered"] is True
        assert result["firmware_version"] == "1.2.3"
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

    def test_a_new_device_is_not_registered_until_a_session_is_opened(self) -> None:
        """Recording an address is not the same as having reached the device."""
        registry = DeviceRegistry()

        device = registry.register_device(node_id="test_node", ip="192.168.1.100")

        assert device.registered is False

    def test_moving_a_device_keeps_its_registered_flag(self) -> None:
        """Whoever opened the session set the flag; a new address does not unset it."""
        registry = DeviceRegistry()
        device = registry.register_device(node_id="test_node", ip="192.168.1.100")
        device.registered = True

        registry.register_device(node_id="test_node", ip="192.168.1.200")

        assert device.registered is True

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
        del devices["node1"]
        assert "node1" in registry.get_all_devices()


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

    def test_device_lock_returns_single_instance(
        self, registry: DeviceRegistry
    ) -> None:
        """Test lock creation and idempotent retrieval."""
        lock1 = registry.device_lock("test_node")
        lock2 = registry.device_lock("test_node")

        assert lock1 is not None
        assert lock1 is lock2

    def test_device_lock_is_per_node(self, registry: DeviceRegistry) -> None:
        """Test that two nodes do not share a lock."""
        assert registry.device_lock("node_a") is not registry.device_lock("node_b")


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


class TestDeviceRegistryRemoveDevice:
    """Test DeviceRegistry remove_device method."""

    def test_remove_device_forgets_everything_about_it(self) -> None:
        """Test removal drops the device, its client and its bookkeeping."""
        registry = DeviceRegistry()
        registry.register_device("test_node", "192.168.1.100")
        registry.mark_discovery_completed("test_node")
        registry.set_client("test_node", MagicMock())

        registry.remove_device("test_node")

        assert registry.get_device("test_node") is None
        assert registry.get_client("test_node") is None
        assert not registry.is_discovery_completed("test_node")

    def test_removing_an_unknown_device_is_harmless(self) -> None:
        """Test removal of a node that was never registered."""
        DeviceRegistry().remove_device("never_seen")

    def test_removal_drops_a_free_lock(self) -> None:
        """A lock nobody holds has nothing left to guard, so it goes with the device."""
        registry = DeviceRegistry()
        registry.register_device("test_node", "192.168.1.100")
        lock = registry.device_lock("test_node")

        registry.remove_device("test_node")

        assert registry.device_lock("test_node") is not lock

    async def test_removal_keeps_a_held_lock(self) -> None:
        """A lock somebody holds stays, or the next registration would not wait on it."""
        registry = DeviceRegistry()
        registry.register_device("test_node", "192.168.1.100")
        lock = registry.device_lock("test_node")

        async with lock:
            registry.remove_device("test_node")
            assert registry.device_lock("test_node") is lock

        assert registry.get_device("test_node") is None


class TestDeviceRegistryAvailability:
    """Test DeviceRegistry availability check methods."""

    def test_has_session_no_client(self) -> None:
        """Test has_session returns False when no client."""
        registry = DeviceRegistry()
        device = registry.register_device("test_node", "192.168.1.100")
        device.registered = True

        # No client set
        result = registry.has_session("test_node")
        assert result is False

    async def test_is_device_available_async_device_not_available(self) -> None:
        """Test is_device_available_async returns False when device not available."""
        registry = DeviceRegistry()
        # Device not registered
        result = await registry.is_device_available_async("test_node")
        assert result is False

    async def test_is_device_available_async_no_client_after_check(self) -> None:
        """Test is_device_available_async returns False when client removed (race)."""
        registry = DeviceRegistry()
        device = registry.register_device("test_node", "192.168.1.100")
        device.registered = True

        # Mock: has_session returns True, but get_client returns None
        # This simulates a race condition where client is removed between checks
        with (
            patch.object(registry, "has_session", return_value=True),
            patch.object(registry, "get_client", return_value=None),
        ):
            result = await registry.is_device_available_async("test_node")

        assert result is False
