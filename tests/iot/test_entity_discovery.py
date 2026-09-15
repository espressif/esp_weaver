# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the entity_discovery module."""

import json
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esp_weaver.helpers.host import HomeAssistantHost
from custom_components.esp_weaver.iot.managers.entity_discovery import (
    BindingResolver,
    DeviceDiscoveryManager,
)
from custom_components.esp_weaver.profiles.binding import EntityBinding
from custom_components.esp_weaver.profiles.registry import ProfileRegistry
from custom_components.esp_weaver.profiles.resolver import resolve_bindings
from custom_components.esp_weaver.profiles.schema import DeviceProfile
from tests.fakes import FakeHost, no_bindings


def _resolver(registry: ProfileRegistry) -> BindingResolver:
    """Return the resolver the integration injects, backed by one registry."""

    async def resolve(
        node_id: str,
        raw_config: dict[str, Any],
        raw_params: dict[str, Any],
    ) -> list[EntityBinding]:
        return resolve_bindings(node_id, raw_config, raw_params, registry)

    return resolve


@pytest.fixture
def mock_host() -> FakeHost:
    """A host that records every binding the manager announces."""
    return FakeHost()


class TestDeviceDiscoveryManagerInit:
    """Test DeviceDiscoveryManager initialization."""

    def test_init(self, mock_host: FakeHost, mock_registry: MagicMock) -> None:
        """Test basic initialization."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

        assert manager.host is mock_host
        assert manager.registry == mock_registry


class TestDeviceDiscoveryManagerParseAndDiscover:
    """Test DeviceDiscoveryManager.parse_and_discover_entities method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with device lookup support."""
        registry = MagicMock()
        registry.get_device.return_value = MagicMock()
        return registry

    @pytest.mark.asyncio
    async def test_parse_and_discover_no_properties(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test with no properties."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

        await manager.parse_and_discover_entities("node123", None)

        # Should not fire any events
        assert mock_host.discovered == []

    @pytest.mark.asyncio
    async def test_parse_and_discover_empty_properties(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test with empty properties list."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

        await manager.parse_and_discover_entities("node123", [])

        assert mock_host.discovered == []

    @pytest.mark.asyncio
    async def test_parse_and_discover_with_config(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test with valid config property."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

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


class TestDeviceDiscoveryManagerParseAndDiscoverEdgeCases:
    """Test parse_and_discover_entities edge cases."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry returning None for device lookup."""
        registry = MagicMock()
        registry.get_device.return_value = None
        return registry

    @pytest.mark.asyncio
    async def test_parse_and_discover_json_decode_error(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test handling of JSONDecodeError."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)
        properties = [{"name": "config", "value": b"invalid json"}]

        # Should not raise
        await manager.parse_and_discover_entities("node123", properties)

        # Should not fire any events due to error
        assert mock_host.discovered == []

    @pytest.mark.asyncio
    async def test_parse_and_discover_no_config_property(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test handling when no config property found."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)
        properties = [{"name": "other", "value": b"{}"}]

        await manager.parse_and_discover_entities("node123", properties)

        assert mock_host.discovered == []

    @pytest.mark.asyncio
    async def test_parse_and_discover_updates_device_registry(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test device registry is updated with parsed config."""
        mock_device = MagicMock()
        mock_registry.get_device.return_value = mock_device

        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

        config_data = {
            "node_id": "node123",
            "info": {"name": "Test Device", "fw_version": "1.0"},
            "devices": [],
        }
        properties = [{"name": "config", "value": json.dumps(config_data).encode()}]

        await manager.parse_and_discover_entities("node123", properties)

        # Device should be updated
        mock_registry.get_device.assert_called_with("node123")

    @pytest.mark.asyncio
    async def test_parse_and_discover_keeps_the_reported_config(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Test the unparsed config and params are kept for diagnostics."""
        mock_device = MagicMock()
        mock_registry.get_device.return_value = mock_device

        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

        config_data = {
            "node_id": "node123",
            "info": {"name": "Test Device"},
            "devices": [
                {
                    "name": "Sensor",
                    "type": "esp.device.sensor",
                    "params": [{"name": "Ambient Temperature"}],
                }
            ],
        }
        params_data = {"Sensor": {"Ambient Temperature": 23.5}}
        properties = [
            {"name": "config", "value": json.dumps(config_data).encode()},
            {"name": "params", "value": json.dumps(params_data).encode()},
        ]

        await manager.parse_and_discover_entities("node123", properties)

        # Stored verbatim: the profile resolver matches on these exact strings.
        assert mock_device.raw_config == config_data
        assert mock_device.raw_params == params_data


class TestBindingDiscovery:
    """Test the profile-driven half of discovery."""

    @staticmethod
    def _registry() -> ProfileRegistry:
        """Build a registry with one switchable device type."""
        registry = ProfileRegistry()
        registry.add_profiles(
            source="test.yaml",
            devices=[
                DeviceProfile.from_dict(
                    {
                        "type": "esp.device.switch",
                        "entities": [
                            {
                                "platform": "switch",
                                "name": "Switch",
                                "key": "power",
                                "attributes": {
                                    "state": {
                                        "param": "Power",
                                        "data_type": "bool",
                                        "properties": ["read", "write"],
                                    }
                                },
                            }
                        ],
                    },
                    "test.yaml",
                )
            ],
            services=[],
        )
        return registry

    @staticmethod
    def _properties() -> list[dict]:
        """Return a node report for one switch device."""
        config_data = {
            "node_id": "node123",
            "info": {"name": "Test Device"},
            "devices": [
                {
                    "name": "Switch",
                    "type": "esp.device.switch",
                    "params": [{"name": "Power"}],
                }
            ],
        }
        return [
            {"name": "config", "value": json.dumps(config_data).encode()},
            {
                "name": "params",
                "value": json.dumps({"Switch": {"Power": True}}).encode(),
            },
        ]

    @pytest.mark.asyncio
    async def test_announces_each_resolved_binding(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Every binding is fired with the report the device already sent."""
        mock_device = MagicMock()
        mock_registry.get_device.return_value = mock_device
        manager = DeviceDiscoveryManager(
            mock_host, mock_registry, _resolver(self._registry())
        )

        await manager.parse_and_discover_entities("node123", self._properties())

        fired = mock_host.discovered_for("node123")
        assert len(fired) == 1
        binding, initial_report = fired[0]
        assert binding.platform == "switch"
        assert binding.key == "power"
        assert initial_report == {"Power": True}

    @pytest.mark.asyncio
    async def test_a_broken_resolver_does_not_stop_discovery(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """A failing resolution costs the bindings, not the reported config."""
        mock_device = MagicMock()
        mock_registry.get_device.return_value = mock_device

        async def explode(
            node_id: str,
            raw_config: dict[str, Any],
            raw_params: dict[str, Any],
        ) -> list[EntityBinding]:
            raise TypeError("boom")

        manager = DeviceDiscoveryManager(mock_host, mock_registry, explode)

        await manager.parse_and_discover_entities("node123", self._properties())

        assert mock_device.raw_config != {}
        assert mock_host.discovered_for("node123") == []


class TestCloudDiscovery:
    """The cloud path, which reshapes cloud data into a node's own report."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with device lookup support."""
        registry = MagicMock()
        registry.get_device.return_value = MagicMock()
        return registry

    @staticmethod
    def _node_config() -> dict:
        """Return what the cloud answers for getnodeconfig."""
        return {
            "node_id": "node123",
            "info": {"name": "Test Device"},
            "devices": [
                {
                    "name": "Switch",
                    "type": "esp.device.switch",
                    "params": [{"name": "Power"}],
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_a_cloud_node_resolves_the_same_profiles(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """Cloud and local differ in where the data came from, nothing else.

        Both end up in parse_and_discover_entities, so a profile that works for
        a local device works for the same device reached through the cloud.
        """
        manager = DeviceDiscoveryManager(
            mock_host,
            mock_registry,
            _resolver(TestBindingDiscovery._registry()),
        )

        await manager.discover_from_cloud(
            "node123", self._node_config(), {"Switch": {"Power": True}}
        )

        fired = mock_host.discovered_for("node123")
        assert len(fired) == 1
        assert fired[0][0].key == "power"
        assert fired[0][1] == {"Power": True}

    @pytest.mark.asyncio
    async def test_a_node_the_cloud_has_no_config_for_yet_announces_nothing(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """The cloud stores a node's config some seconds after provisioning."""
        manager = DeviceDiscoveryManager(mock_host, mock_registry, no_bindings)

        await manager.discover_from_cloud("node123", {}, {})

        assert mock_host.discovered == []

    @pytest.mark.asyncio
    async def test_a_node_with_no_parameters_yet_still_gets_its_entities(
        self, mock_host: FakeHost, mock_registry: MagicMock
    ) -> None:
        """What entities exist follows the config; values follow separately."""
        manager = DeviceDiscoveryManager(
            mock_host,
            mock_registry,
            _resolver(TestBindingDiscovery._registry()),
        )

        await manager.discover_from_cloud("node123", self._node_config(), {})

        fired = mock_host.discovered_for("node123")
        assert len(fired) == 1
        assert fired[0][1] == {}


class TestStaleEntityRemoval:
    """Reflashing a board with a different set of devices."""

    @staticmethod
    def _manager(
        hass: HomeAssistant,
        profiles: ProfileRegistry | None = None,
    ) -> DeviceDiscoveryManager:
        """Build a manager for the node the tests' config entry is for."""
        registry = MagicMock()
        registry.get_device.return_value = MagicMock()
        return DeviceDiscoveryManager(
            HomeAssistantHost(hass),
            registry,
            _resolver(profiles or TestBindingDiscovery._registry()),
        )

    @staticmethod
    def _register(entities: er.EntityRegistry, entry: MockConfigEntry, key: str) -> str:
        """Put one entity in the registry the way discovery would have."""
        return entities.async_get_or_create(
            "switch",
            "esp_weaver",
            f"node123_{key}",
            config_entry=entry,
        ).entity_id

    async def test_an_entity_the_firmware_dropped_is_removed(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing else removes a stale entity: it reads as available."""
        entry = MockConfigEntry(domain="esp_weaver", unique_id="node123")
        entry.add_to_hass(hass)
        entities = er.async_get(hass)
        gone = self._register(entities, entry, "old_thermostat")
        kept = self._register(entities, entry, "power")

        await self._manager(hass).parse_and_discover_entities(
            "node123", TestBindingDiscovery._properties()
        )

        assert entities.async_get(gone) is None
        assert entities.async_get(kept) is not None

    async def test_a_node_that_resolved_nothing_removes_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """A resolution producing nothing is far more often a bad answer.

        Removing every entity on the strength of one would be a poor trade.
        """
        entry = MockConfigEntry(domain="esp_weaver", unique_id="node123")
        entry.add_to_hass(hass)
        entities = er.async_get(hass)
        existing = self._register(entities, entry, "power")

        manager = self._manager(hass, ProfileRegistry())
        await manager.parse_and_discover_entities(
            "node123", TestBindingDiscovery._properties()
        )

        assert entities.async_get(existing) is not None

    async def test_an_entity_moved_to_another_platform_leaves_nothing_behind(
        self, hass: HomeAssistant
    ) -> None:
        """Editing a profile's platform keeps the key, so the key is not enough.

        Matching on it alone left the old switch in place beside the new light,
        both claiming to be the same thing.
        """
        entry = MockConfigEntry(domain="esp_weaver", unique_id="node123")
        entry.add_to_hass(hass)
        entities = er.async_get(hass)
        # Registered as a switch, which is what the profile used to say.
        was_a_switch = self._register(entities, entry, "power")

        registry = ProfileRegistry()
        registry.add_profiles(
            source="test.yaml",
            devices=[
                DeviceProfile.from_dict(
                    {
                        "type": "esp.device.switch",
                        "entities": [
                            {
                                "platform": "light",
                                "name": "Switch",
                                "key": "power",
                                "attributes": {
                                    "state": {
                                        "param": "Power",
                                        "data_type": "bool",
                                        "properties": ["read", "write"],
                                    }
                                },
                            }
                        ],
                    },
                    "test.yaml",
                )
            ],
            services=[],
        )

        manager = self._manager(hass, registry)
        await manager.parse_and_discover_entities(
            "node123", TestBindingDiscovery._properties()
        )

        assert entities.async_get(was_a_switch) is None

    async def test_another_node_sharing_the_entry_is_left_alone(
        self, hass: HomeAssistant
    ) -> None:
        """Only this node's own entities are this node's to forget."""
        entry = MockConfigEntry(domain="esp_weaver", unique_id="node123")
        entry.add_to_hass(hass)
        entities = er.async_get(hass)
        other = entities.async_get_or_create(
            "switch",
            "esp_weaver",
            "othernode_power",
            config_entry=entry,
        ).entity_id

        await self._manager(hass).parse_and_discover_entities(
            "node123", TestBindingDiscovery._properties()
        )

        assert entities.async_get(other) is not None


class TestRememberReport:
    """A report is kept as the node's latest values, and resolves nothing."""

    @staticmethod
    def _manager(host: FakeHost, device: MagicMock | None) -> DeviceDiscoveryManager:
        """Build a manager over a registry that knows one node, or none."""
        registry = MagicMock()
        registry.get_device.return_value = device
        return DeviceDiscoveryManager(host, registry, _resolver(ProfileRegistry()))

    def test_the_record_is_updated(self, mock_host: FakeHost) -> None:
        """Diagnostics and the options flow read the node's latest values."""
        device = MagicMock()
        device.raw_params = {"Light": {"Brightness": 10}}
        manager = self._manager(mock_host, device)
        report = {"Light": {"Brightness": 80}}

        manager.remember_report("node123", report)

        assert device.raw_params == report
        assert mock_host.discovered == []

    def test_an_unknown_node_is_left_alone(self, mock_host: FakeHost) -> None:
        """A report can outlive the registry entry it belongs to."""
        manager = self._manager(mock_host, None)

        manager.remember_report("node123", {"Light": {"Brightness": 80}})

        assert mock_host.discovered == []
