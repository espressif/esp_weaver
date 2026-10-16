# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the property_manager module."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.esp_weaver.iot.client.client import ESPLocalCtrlClient
from custom_components.esp_weaver.iot.managers.property_manager import (
    ConnectionCallbacks,
    PropertyManager,
    WriteOutcome,
)


class TestConnectionCallbacks:
    """Test ConnectionCallbacks dataclass."""

    def test_creation(self) -> None:
        """Test creating connection callbacks."""
        establish = AsyncMock()
        reconnect = AsyncMock()

        callbacks = ConnectionCallbacks(
            establish_connection=establish,
            reconnect_and_retry_params=reconnect,
        )

        assert callbacks.establish_connection == establish
        assert callbacks.reconnect_and_retry_params == reconnect


class TestPropertyManagerInit:
    """Test PropertyManager initialization."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with default_port for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    def test_basic_init(self, mock_registry: MagicMock) -> None:
        """Test basic initialization."""
        manager = PropertyManager(mock_registry)

        assert manager.registry == mock_registry
        assert manager._event_dispatcher is None
        assert manager._connection_callbacks is None

    def test_init_with_dispatcher(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test initialization with event dispatcher."""
        dispatcher = MagicMock()
        manager = PropertyManager(mock_registry, event_dispatcher=dispatcher)

        assert manager._event_dispatcher == dispatcher

    def test_starts_without_callbacks(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Connection callbacks are wired after construction, never through it."""
        manager = PropertyManager(mock_registry, event_dispatcher=MagicMock())

        assert manager._connection_callbacks is None

    def test_set_connection_callbacks(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test setting connection callbacks after initialization."""
        manager = PropertyManager(mock_registry)
        callbacks = ConnectionCallbacks(
            establish_connection=AsyncMock(),
            reconnect_and_retry_params=AsyncMock(),
        )

        manager.set_connection_callbacks(callbacks)

        assert manager._connection_callbacks == callbacks


class TestPropertyManagerSetParams:
    """Test PropertyManager.set_params, the profile-driven write path."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with a connected client."""
        registry = MagicMock()
        registry.default_port = 8080
        registry.has_client.return_value = True
        return registry

    @pytest.fixture
    def mock_client(self) -> MagicMock:
        """Create mock ESP client that is connected and accepts writes."""
        client = MagicMock(spec=ESPLocalCtrlClient)
        client.set_property_values = AsyncMock(return_value=True)
        client.is_connected = AsyncMock(return_value=True)
        return client

    @staticmethod
    def _with_callbacks(
        manager: PropertyManager, retry: AsyncMock | None = None
    ) -> AsyncMock:
        """Wire connection callbacks into the manager and return the retry mock."""
        retry = retry or AsyncMock(return_value=WriteOutcome.ACCEPTED)
        manager.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=AsyncMock(),
                reconnect_and_retry_params=retry,
            )
        )
        return retry

    async def test_sends_the_payload_verbatim(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """The device name from the binding survives to the wire."""
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_registry)

        result = await manager.set_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.ACCEPTED
        _, values = mock_client.set_property_values.call_args.args
        assert json.loads(values[0]) == {"Outlet": {"Power": True}}

    async def test_echoes_the_write_back(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """A successful write is dispatched so the entity confirms its state."""
        mock_registry.get_client.return_value = mock_client
        dispatcher = MagicMock()
        manager = PropertyManager(mock_registry, event_dispatcher=dispatcher)

        await manager.set_params("node123", {"Outlet": {"Power": True}})

        dispatcher.assert_called_once_with("node123", {"Outlet": {"Power": True}})

    async def test_empty_payload_is_not_sent(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """A read-only binding produces no command, and no write."""
        manager = PropertyManager(mock_registry)

        assert await manager.set_params("node123", {}) is WriteOutcome.REFUSED
        mock_registry.get_client.assert_not_called()

    async def test_a_lost_connection_reconnects_and_replays(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """A False from a client that is no longer connected is a lost connection."""
        mock_client.set_property_values = AsyncMock(return_value=False)
        mock_client.is_connected = AsyncMock(return_value=False)
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_registry)
        retry = self._with_callbacks(manager)

        result = await manager.set_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.ACCEPTED
        retry.assert_awaited_once_with("node123", {"Outlet": {"Power": True}})

    async def test_a_refusal_from_a_connected_device_does_not_reconnect(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """A False from a client that is still connected is the device saying no."""
        mock_client.set_property_values = AsyncMock(return_value=False)
        mock_client.is_connected = AsyncMock(return_value=True)
        mock_registry.get_client.return_value = mock_client
        dispatcher = MagicMock()
        manager = PropertyManager(mock_registry, event_dispatcher=dispatcher)
        retry = self._with_callbacks(manager)

        result = await manager.set_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.REFUSED
        retry.assert_not_awaited()
        dispatcher.assert_not_called()

    async def test_without_a_retry_callback_it_gives_up(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """No connection callbacks wired up means a lost connection simply fails."""
        mock_client.set_property_values = AsyncMock(return_value=False)
        mock_client.is_connected = AsyncMock(return_value=False)
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_registry)

        result = await manager.set_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.UNREACHABLE

    async def test_without_a_client_it_fails(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """A node with no client, and no way to make one, cannot be written."""
        mock_registry.has_client.return_value = False
        mock_registry.get_client.return_value = None
        manager = PropertyManager(mock_registry)

        result = await manager.set_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.UNREACHABLE

    async def test_an_unserialisable_payload_fails_without_sending(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """What json cannot encode never reaches the device."""
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_registry)

        result = await manager.set_params("node123", {"Outlet": {"Power": object()}})

        assert result is WriteOutcome.REFUSED
        mock_client.set_property_values.assert_not_awaited()


class TestPropertyManagerRetryParams:
    """Test PropertyManager.retry_params, the one-shot replay after reconnecting."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry with a connected client."""
        registry = MagicMock()
        registry.default_port = 8080
        registry.has_client.return_value = True
        return registry

    @pytest.fixture
    def mock_client(self) -> MagicMock:
        """Create mock ESP client that is connected."""
        client = MagicMock(spec=ESPLocalCtrlClient)
        client.is_connected = AsyncMock(return_value=True)
        return client

    async def test_a_successful_replay_is_echoed(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """The replayed write is dispatched so the entity confirms its state."""
        mock_client.set_property_values = AsyncMock(return_value=True)
        mock_registry.get_client.return_value = mock_client
        dispatcher = MagicMock()
        manager = PropertyManager(mock_registry, event_dispatcher=dispatcher)

        result = await manager.retry_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.ACCEPTED
        dispatcher.assert_called_once_with("node123", {"Outlet": {"Power": True}})

    async def test_a_refused_replay_fails_without_reconnecting(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """A device that refuses again is answered REFUSED, not asked again."""
        mock_client.set_property_values = AsyncMock(return_value=False)
        mock_registry.get_client.return_value = mock_client
        dispatcher = MagicMock()
        manager = PropertyManager(mock_registry, event_dispatcher=dispatcher)
        retry = AsyncMock(return_value=WriteOutcome.ACCEPTED)
        manager.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=AsyncMock(),
                reconnect_and_retry_params=retry,
            )
        )

        result = await manager.retry_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.REFUSED
        retry.assert_not_awaited()
        dispatcher.assert_not_called()

    async def test_a_connection_lost_again_fails_without_reconnecting(
        self,
        mock_hass: MagicMock,
        mock_registry: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """Even a lost connection is not rebuilt a second time from here."""
        mock_client.set_property_values = AsyncMock(return_value=False)
        mock_client.is_connected = AsyncMock(return_value=False)
        mock_registry.get_client.return_value = mock_client
        manager = PropertyManager(mock_registry)
        retry = AsyncMock(return_value=WriteOutcome.ACCEPTED)
        manager.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=AsyncMock(),
                reconnect_and_retry_params=retry,
            )
        )

        result = await manager.retry_params("node123", {"Outlet": {"Power": True}})

        assert result is WriteOutcome.UNREACHABLE
        retry.assert_not_awaited()


class TestPropertyManagerProcessPropertyUpdate:
    """Test PropertyManager.process_property_update method."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    def test_process_with_dispatcher(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing property update fires event."""
        dispatcher = MagicMock()
        manager = PropertyManager(mock_registry, event_dispatcher=dispatcher)

        manager.process_property_update("node123", {"Light": {"Power": True}})

        dispatcher.assert_called_once_with("node123", {"Light": {"Power": True}})

    def test_process_without_dispatcher(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test processing property update without dispatcher."""
        manager = PropertyManager(mock_registry)

        # Should not raise
        manager.process_property_update("node123", {"Light": {"Power": True}})


class TestPropertyManagerEdgeCases:
    """Test PropertyManager edge cases and error handling."""

    @pytest.fixture
    def mock_registry(self) -> MagicMock:
        """Create mock device registry for this test class."""
        registry = MagicMock()
        registry.default_port = 8080
        return registry

    async def test_establish_connection_device_not_found(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test establish_connection returns False when device not found."""
        mock_registry.get_device.return_value = None
        manager = PropertyManager(mock_registry)
        manager.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=AsyncMock(),
                reconnect_and_retry_params=AsyncMock(),
            )
        )

        result = await manager._establish_connection_for_set("unknown_node")

        assert result is False

    async def test_establish_connection_device_missing_ip_or_port(
        self, mock_hass: MagicMock, mock_registry: MagicMock
    ) -> None:
        """Test establish_connection returns False when device missing IP or port."""
        mock_device = MagicMock()
        mock_device.ip = None
        mock_device.port = 8080
        mock_registry.get_device.return_value = mock_device

        manager = PropertyManager(mock_registry)
        manager.set_connection_callbacks(
            ConnectionCallbacks(
                establish_connection=AsyncMock(),
                reconnect_and_retry_params=AsyncMock(),
            )
        )

        result = await manager._establish_connection_for_set("node123")

        assert result is False
