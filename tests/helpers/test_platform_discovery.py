# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the wiring that turns resolved bindings into a platform's entities."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.esp_weaver.helpers.discovery import setup_binding_platform
from custom_components.esp_weaver.helpers.signals import (
    signal_binding_discovered,
    signal_binding_removed,
)
from custom_components.esp_weaver.profiles import EntityBinding
from tests.conftest import TEST_NODE_ID, create_mock_config_entry


def _create_binding(
    platform: str = "sensor",
    key: str = "temperature",
) -> EntityBinding:
    """Create a minimal resolved binding for discovery tests."""
    return EntityBinding(
        node_id=TEST_NODE_ID,
        device_name="Temperature Sensor",
        device_type="esp.device.temperature-sensor",
        platform=platform,
        name="Temperature",
        key=key,
    )


class _RecordingEntity:
    """Stand-in for a platform entity that remembers how it was built."""

    built: list["_RecordingEntity"] = []

    def __init__(self, **kwargs: Any) -> None:
        """Record the arguments the discovery wiring passed."""
        self.kwargs = kwargs
        type(self).built.append(self)


def _setup(
    hass: HomeAssistant,
    entry: Any,
    entity_cls: Any,
    platform_name: str = "sensor",
) -> MagicMock:
    """Wire a platform and return the async_add_entities mock it was given."""
    async_add_entities = MagicMock()
    setup_binding_platform(
        hass,
        entry,
        async_add_entities,
        platform_name,
        entity_cls,
    )
    return async_add_entities


class TestPlatformSetup:
    """Test how setup_binding_platform ties itself to the config entry."""

    async def test_listener_is_tied_to_the_entry(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test the binding listener is unregistered with the entry."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        _setup(hass, entry, _RecordingEntity)

        entry.async_on_unload.assert_called()

    async def test_cache_is_keyed_by_platform_name(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test each platform gets its own cache under its own name."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}

        _setup(hass, entry, _RecordingEntity, platform_name="sensor")
        _setup(hass, entry, _RecordingEntity, platform_name="custom_platform")

        assert set(mock_coordinator.discovered_entities) == {
            "sensor",
            "custom_platform",
        }


class TestBindingDiscovery:
    """Test how a discovered binding becomes an entity."""

    async def test_builds_and_tracks_entity(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test an entity is built from the binding and its current report."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        binding = _create_binding()
        _RecordingEntity.built = []

        async_add_entities = _setup(hass, entry, _RecordingEntity)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            binding,
            {"Temperature Sensor": {"temperature": 25.5}},
        )
        await hass.async_block_till_done()

        assert len(_RecordingEntity.built) == 1
        entity = _RecordingEntity.built[0]
        assert entity.kwargs["binding"] is binding
        assert entity.kwargs["report"] == {"Temperature Sensor": {"temperature": 25.5}}
        assert entity.kwargs["coordinator"] is mock_coordinator
        assert entity.kwargs["device_name"] == entry.title

        assert mock_coordinator.discovered_entities["sensor"] == {binding.key: entity}
        async_add_entities.assert_called_once_with([entity])

    async def test_empty_report_when_event_carries_none(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test an entity still builds when the event has no report."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        _RecordingEntity.built = []

        _setup(hass, entry, _RecordingEntity)

        async_dispatcher_send(
            hass, signal_binding_discovered(TEST_NODE_ID), _create_binding(), {}
        )
        await hass.async_block_till_done()

        assert [e.kwargs["report"] for e in _RecordingEntity.built] == [{}]

    async def test_ignores_other_node(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test bindings discovered on another node are left alone."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        _RecordingEntity.built = []

        async_add_entities = _setup(hass, entry, _RecordingEntity)

        async_dispatcher_send(
            hass, signal_binding_discovered("different_node"), _create_binding(), {}
        )
        await hass.async_block_till_done()

        assert _RecordingEntity.built == []
        async_add_entities.assert_not_called()

    async def test_ignores_other_platform(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test bindings belonging to another platform are left alone."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        _RecordingEntity.built = []

        async_add_entities = _setup(hass, entry, _RecordingEntity)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _create_binding(platform="light", key="light"),
            {},
        )
        await hass.async_block_till_done()

        assert _RecordingEntity.built == []
        async_add_entities.assert_not_called()

    async def test_binding_announced_twice_builds_once(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Test a repeated binding does not produce a second entity."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        binding = _create_binding()
        _RecordingEntity.built = []

        async_add_entities = _setup(hass, entry, _RecordingEntity)

        for _ in range(2):
            async_dispatcher_send(
                hass, signal_binding_discovered(TEST_NODE_ID), binding, {}
            )
            await hass.async_block_till_done()

        assert len(_RecordingEntity.built) == 1
        assert async_add_entities.call_count == 1


class TestBindingRemoval:
    """Test how a platform lets go of an entity the node stopped describing."""

    async def test_the_cache_gives_the_key_back(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """A key held after removal is a key nothing can be built under again."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        _RecordingEntity.built = []

        async_add_entities = _setup(hass, entry, _RecordingEntity)
        binding = _create_binding(key="scene_ab3")

        async_dispatcher_send(
            hass, signal_binding_discovered(TEST_NODE_ID), binding, {}
        )
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, signal_binding_removed(TEST_NODE_ID), "sensor", "scene_ab3"
        )
        await hass.async_block_till_done()

        assert mock_coordinator.discovered_entities["sensor"] == {}

        # The same key again, as a scene deleted and recreated on the device.
        async_dispatcher_send(
            hass, signal_binding_discovered(TEST_NODE_ID), binding, {}
        )
        await hass.async_block_till_done()

        assert async_add_entities.call_count == 2

    async def test_another_platform_keeps_its_own(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Two platforms can hold the same key, so the platform has to match."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        _RecordingEntity.built = []

        _setup(hass, entry, _RecordingEntity, platform_name="sensor")

        async_dispatcher_send(
            hass, signal_binding_discovered(TEST_NODE_ID), _create_binding(), {}
        )
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, signal_binding_removed(TEST_NODE_ID), "switch", "temperature"
        )
        await hass.async_block_till_done()

        assert set(mock_coordinator.discovered_entities["sensor"]) == {"temperature"}

    async def test_another_node_keeps_its_own(
        self,
        hass: HomeAssistant,
        mock_config_entry_data: dict[str, Any],
        mock_coordinator: MagicMock,
    ) -> None:
        """Keys are unique within a node, not across them."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator
        mock_coordinator.discovered_entities = {}
        _RecordingEntity.built = []

        _setup(hass, entry, _RecordingEntity)

        async_dispatcher_send(
            hass, signal_binding_discovered(TEST_NODE_ID), _create_binding(), {}
        )
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, signal_binding_removed("some_other_node"), "sensor", "temperature"
        )
        await hass.async_block_till_done()

        assert set(mock_coordinator.discovered_entities["sensor"]) == {"temperature"}
