# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver device API module."""

import asyncio
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from google.protobuf.message import DecodeError
import pytest

from custom_components.esp_weaver.iot.client.device_api import ESPWeaverApi
from custom_components.esp_weaver.iot.local_ctrl import local_ctrl
from custom_components.esp_weaver.iot.managers.property_manager import WriteOutcome
from tests.fakes import FakeHost, no_bindings

# Test constants
TEST_NODE_ID = "test_node"
TEST_IP_ADDRESS = "192.168.1.100"
TEST_PORT = 8080


@pytest.fixture
def host() -> FakeHost:
    """A host that records what the API asks of it."""
    return FakeHost()


@pytest.fixture
def api(host: FakeHost) -> ESPWeaverApi:
    """Create an ESPWeaverApi instance for testing."""
    return ESPWeaverApi(host=host, resolve_bindings=no_bindings)


class TestESPWeaverApiInit:
    """Test ESPWeaverApi initialization."""

    def test_basic_initialization(
        self,
        host: FakeHost,
    ) -> None:
        """Test basic API initialization."""
        api = ESPWeaverApi(host=host, resolve_bindings=no_bindings)

        assert api.host is host
        assert api.default_port == TEST_PORT
        assert api.registry is not None

    def test_initialization_with_custom_port(
        self,
        host: FakeHost,
    ) -> None:
        """Test API initialization with custom port."""
        api = ESPWeaverApi(
            host=host,
            resolve_bindings=no_bindings,
            default_port=9090,
        )

        assert api.default_port == 9090

    def test_initialization_with_event_dispatcher(
        self,
        host: FakeHost,
    ) -> None:
        """Test API initialization with event dispatcher wiring."""
        dispatcher = MagicMock()
        api = ESPWeaverApi(
            host=host,
            resolve_bindings=no_bindings,
            event_dispatcher=dispatcher,
        )

        # Verify wiring by invoking the public API method that dispatches events
        test_params = {"test_key": "test_value"}
        api._property.process_property_update(TEST_NODE_ID, test_params)

        # Assert the dispatcher was invoked with expected args
        dispatcher.assert_called_once_with(TEST_NODE_ID, test_params)


class TestESPWeaverApiDeviceAvailability:
    """Test ESPWeaverApi device availability methods."""

    async def test_is_device_available_async(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_device_available_async method."""
        device = api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
        device.registered = True

        # Create mock client with required attributes
        mock_client = MagicMock()
        mock_client.session_established = True
        mock_client.transport = MagicMock()
        mock_client.is_connected = AsyncMock(return_value=True)
        api.registry.set_client(TEST_NODE_ID, mock_client)

        result = await api.is_device_available_async(TEST_NODE_ID)
        assert result is True

    async def test_is_mdns_available(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_mdns_available method."""
        with patch.object(
            api._availability,
            "check_device_mdns_available",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_check:
            result = await api.is_mdns_available(TEST_NODE_ID, TEST_IP_ADDRESS)

            assert result is True
            mock_check.assert_called_once_with(TEST_NODE_ID, TEST_IP_ADDRESS)


class TestESPWeaverApiPublishing:
    """The one route a node's report takes to its entities and to discovery."""

    @pytest.fixture
    def published(self, host: FakeHost) -> tuple[ESPWeaverApi, MagicMock]:
        """An API whose dispatcher and discovery are both watched."""
        dispatcher = MagicMock()
        api = ESPWeaverApi(
            host=host, resolve_bindings=no_bindings, event_dispatcher=dispatcher
        )
        api._discovery.remember_report = MagicMock()  # type: ignore[method-assign]
        return api, dispatcher

    async def test_params_reach_the_entities_and_discovery(
        self, published: tuple[ESPWeaverApi, MagicMock]
    ) -> None:
        """Entities read a report for their state; the node keeps it as its latest."""
        api, dispatcher = published
        params = {"Light": {"Power": True}}

        await api.publish_params(TEST_NODE_ID, params)

        dispatcher.assert_called_once_with(TEST_NODE_ID, params)
        api._discovery.remember_report.assert_called_once_with(  # type: ignore[attr-defined]
            TEST_NODE_ID, params
        )

    async def test_raw_properties_are_decoded_first(
        self, published: tuple[ESPWeaverApi, MagicMock]
    ) -> None:
        """What the device sends is decoded into params and published."""
        api, dispatcher = published
        properties = [
            {
                "name": "params",
                "value": b'{"Light": {"Power": true, "Brightness": 100}}',
            }
        ]

        await api.publish_properties(TEST_NODE_ID, properties)

        dispatcher.assert_called_once_with(
            TEST_NODE_ID, {"Light": {"Power": True, "Brightness": 100}}
        )

    async def test_properties_without_params_publish_nothing(
        self, published: tuple[ESPWeaverApi, MagicMock]
    ) -> None:
        """An answer carrying no params is nothing to tell anyone."""
        api, dispatcher = published

        await api.publish_properties(TEST_NODE_ID, [])

        dispatcher.assert_not_called()
        api._discovery.remember_report.assert_not_called()  # type: ignore[attr-defined]

    async def test_an_active_report_is_published(
        self, published: tuple[ESPWeaverApi, MagicMock]
    ) -> None:
        """A report the device pushes goes the same way as one that was read."""
        api, dispatcher = published
        handler = api._active_report_handler(TEST_NODE_ID)

        await handler(
            local_ctrl.MessageSource.ACTIVE_REPORT,
            {
                "properties": [
                    {"name": "params", "value": b'{"Light": {"Power": true}}'}
                ]
            },
        )

        dispatcher.assert_called_once_with(TEST_NODE_ID, {"Light": {"Power": True}})

    async def test_a_query_response_is_not_a_report(
        self, published: tuple[ESPWeaverApi, MagicMock]
    ) -> None:
        """Answers to queries are taken by their futures, not published."""
        api, dispatcher = published
        handler = api._active_report_handler(TEST_NODE_ID)

        await handler(local_ctrl.MessageSource.QUERY_RESPONSE, {"count": 2})

        dispatcher.assert_not_called()

    async def test_a_malformed_active_report_is_logged(
        self,
        published: tuple[ESPWeaverApi, MagicMock],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A push without properties is said so and dropped."""
        api, dispatcher = published
        handler = api._active_report_handler(TEST_NODE_ID)

        await handler(local_ctrl.MessageSource.ACTIVE_REPORT, {"count": 2})

        dispatcher.assert_not_called()
        assert "ACTIVE_REPORT invalid format" in caplog.text


class TestESPWeaverApiEntityDiscovery:
    """Test ESPWeaverApi entity discovery methods."""

    def test_is_discovery_completed(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_discovery_completed method."""
        # Initially not completed
        result = api.is_discovery_completed(TEST_NODE_ID)
        assert result is False

    def test_mark_discovery_completed(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test mark_discovery_completed method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        api.mark_discovery_completed(TEST_NODE_ID)

        result = api.is_discovery_completed(TEST_NODE_ID)
        assert result is True

    async def test_parse_and_discover_entities(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test parse_and_discover_entities method."""
        with patch.object(
            api._discovery,
            "parse_and_discover_entities",
            new_callable=AsyncMock,
        ) as mock_parse:
            await api.parse_and_discover_entities(
                TEST_NODE_ID, [{"name": "config", "value": b"{}"}]
            )

            mock_parse.assert_called_once_with(
                TEST_NODE_ID, [{"name": "config", "value": b"{}"}]
            )


class TestESPWeaverApiPropertyOperations:
    """Test ESPWeaverApi property operations."""

    async def test_set_params_local_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test set_params hands the payload to local control untouched."""
        payload = {"Outlet": {"Power": True}}

        with patch.object(
            api._property,
            "set_params",
            new_callable=AsyncMock,
            return_value=WriteOutcome.ACCEPTED,
        ) as mock_set:
            result = await api.set_params(TEST_NODE_ID, payload)

            assert result is WriteOutcome.ACCEPTED
            mock_set.assert_called_once_with(TEST_NODE_ID, payload)

    async def test_set_params_cloud_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test set_params posts the payload straight to the cloud."""
        payload = {"Outlet": {"Power": True}}
        mock_cloud_client = MagicMock()
        mock_cloud_client.set_params = AsyncMock(return_value=True)
        api.set_cloud_client(TEST_NODE_ID, mock_cloud_client)
        api.set_connection_mode(TEST_NODE_ID, "cloud")

        result = await api.set_params(TEST_NODE_ID, payload)

        assert result is WriteOutcome.ACCEPTED
        mock_cloud_client.set_params.assert_called_once_with(TEST_NODE_ID, payload)

    async def test_set_params_rejects_an_empty_payload(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test an empty payload never reaches the device."""
        with patch.object(
            api._property, "set_params", new_callable=AsyncMock
        ) as mock_set:
            assert await api.set_params(TEST_NODE_ID, {}) is WriteOutcome.REFUSED
            mock_set.assert_not_called()

    async def test_reconnect_and_retry_params(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test the params retry replays the payload after reconnecting."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)
        payload = {"Outlet": {"Power": True}}

        with (
            patch.object(
                api._connection,
                "connect_and_sync",
                new_callable=AsyncMock,
                return_value=(True, []),
            ),
            patch.object(
                api._property,
                "retry_params",
                new_callable=AsyncMock,
                return_value=WriteOutcome.ACCEPTED,
            ) as mock_retry,
        ):
            result = await api._reconnect_and_retry_params(TEST_NODE_ID, payload)

            assert result is WriteOutcome.ACCEPTED
            mock_retry.assert_awaited_once_with(TEST_NODE_ID, payload)

    async def test_reconnect_and_retry_params_no_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test the params retry gives up when the device is unknown."""
        result = await api._reconnect_and_retry_params(
            TEST_NODE_ID, {"Outlet": {"Power": True}}
        )

        assert result is WriteOutcome.UNREACHABLE

    async def test_reconnect_and_retry_params_connect_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test the params retry is skipped when reconnection fails."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)

        with (
            patch.object(
                api._connection,
                "connect_and_sync",
                new_callable=AsyncMock,
                return_value=(False, None),
            ),
            patch.object(
                api._property,
                "retry_params",
                new_callable=AsyncMock,
            ) as mock_retry,
        ):
            result = await api._reconnect_and_retry_params(
                TEST_NODE_ID, {"Outlet": {"Power": True}}
            )

        assert result is WriteOutcome.UNREACHABLE
        mock_retry.assert_not_called()

    async def test_reconnect_and_retry_params_retry_fails(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test the params retry reports failure when the replay fails."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)

        with (
            patch.object(
                api._connection,
                "connect_and_sync",
                new_callable=AsyncMock,
                return_value=(True, []),
            ),
            patch.object(
                api._property,
                "retry_params",
                new_callable=AsyncMock,
                return_value=WriteOutcome.REFUSED,
            ),
        ):
            result = await api._reconnect_and_retry_params(
                TEST_NODE_ID, {"Outlet": {"Power": True}}
            )

        assert result is WriteOutcome.REFUSED

    async def test_a_device_that_keeps_refusing_reconnects_only_once(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """A write refused after reconnection fails; it does not reconnect again."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)
        client = MagicMock()
        client.set_property_values = AsyncMock(return_value=False)
        client.is_connected = AsyncMock(return_value=True)
        api.registry.set_client(TEST_NODE_ID, client)

        with patch.object(
            api._connection,
            "connect_and_sync",
            new_callable=AsyncMock,
            return_value=(True, []),
        ) as mock_connect:
            result = await api._reconnect_and_retry_params(
                TEST_NODE_ID, {"Outlet": {"Power": True}}
            )

        assert result is WriteOutcome.REFUSED
        mock_connect.assert_awaited_once()
        client.set_property_values.assert_awaited_once()

    def test_get_device_returns_what_the_registry_holds(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """A registered node is answered with its DeviceInfo; an unknown one with None."""
        assert api.get_device(TEST_NODE_ID) is None

        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT)

        device = api.get_device(TEST_NODE_ID)
        assert device is not None
        assert device.ip == TEST_IP_ADDRESS
        assert device.port == TEST_PORT


class TestESPWeaverApiDeviceRegistration:
    """Test ESPWeaverApi device registration methods."""

    async def test_register_device_already_connected(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device when already connected."""
        # Register device first
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        # Create mock client that is connected
        mock_client = MagicMock()
        mock_client.is_connected = AsyncMock(return_value=True)
        api.registry.set_client(TEST_NODE_ID, mock_client)

        result = await api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        assert result is True

    async def test_register_device_new_connection(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device with new connection."""
        with patch.object(
            api,
            "_connect_and_sync_device",
            new_callable=AsyncMock,
            return_value=(True, []),
        ):
            result = await api.register_device(
                TEST_NODE_ID, TEST_IP_ADDRESS, port=TEST_PORT
            )

            assert result is True

    async def test_register_device_connection_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device when connection fails."""
        with patch.object(
            api,
            "_connect_and_sync_device",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = await api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

            assert result is False

    @pytest.mark.parametrize(
        "error",
        [ValueError("bad public key"), DecodeError("truncated"), KeyError("x")],
        ids=["value", "decode", "key"],
    )
    async def test_an_unexpected_handshake_error_settles_the_registration(
        self,
        api: ESPWeaverApi,
        error: Exception,
    ) -> None:
        """Whatever the handshake raises, later callers must not hang forever."""
        with (
            patch.object(
                api,
                "_connect_and_sync_device",
                new_callable=AsyncMock,
                side_effect=error,
            ),
            pytest.raises(type(error)),
        ):
            await api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        assert TEST_NODE_ID not in api._registering
        device = api.registry.get_device(TEST_NODE_ID)
        assert device is not None
        assert device.registered is False

        # A second registration starts fresh instead of awaiting the dead one.
        with patch.object(
            api,
            "_connect_and_sync_device",
            new_callable=AsyncMock,
            return_value=(True, []),
        ):
            assert await asyncio.wait_for(
                api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS), timeout=1
            )

    async def test_a_waiter_sees_the_owners_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """The second caller gets the same exception as the one doing the work."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_connect(*_args: Any, **_kwargs: Any) -> tuple[bool, list]:
            started.set()
            await release.wait()
            raise ValueError("bad public key")

        with patch.object(api, "_connect_and_sync_device", side_effect=slow_connect):
            owner = asyncio.ensure_future(
                api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
            )
            await started.wait()
            waiter = asyncio.ensure_future(
                api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
            )
            await asyncio.sleep(0)
            release.set()

            results = await asyncio.gather(owner, waiter, return_exceptions=True)

        assert all(isinstance(result, ValueError) for result in results)
        assert TEST_NODE_ID not in api._registering

    async def test_cleanup_during_a_handshake_does_not_blow_up_the_owner(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """cleanup() cancels the in-flight future; the owner must cope with that."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_connect(*_args: Any, **_kwargs: Any) -> tuple[bool, list]:
            started.set()
            await release.wait()
            return True, []

        with (
            patch.object(api, "_connect_and_sync_device", side_effect=slow_connect),
            patch.object(api._connection, "disconnect_all", new_callable=AsyncMock),
        ):
            owner = asyncio.ensure_future(
                api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
            )
            await started.wait()
            await api.cleanup()
            release.set()

            assert await owner is True

    async def test_unregister_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test unregister_device method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        with patch.object(
            api._connection,
            "disconnect_device",
            new_callable=AsyncMock,
        ) as mock_disconnect:
            await api.unregister_device(TEST_NODE_ID)

            mock_disconnect.assert_called_once_with(TEST_NODE_ID)


class TestESPWeaverApiCleanup:
    """Test ESPWeaverApi cleanup methods."""

    async def test_cleanup_stops_browser(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test cleanup stops mDNS browser."""
        mock_browser = MagicMock()
        mock_browser.cancel = MagicMock()
        api._global_browser = mock_browser

        with patch.object(
            api._connection,
            "disconnect_all",
            new_callable=AsyncMock,
        ):
            await api.cleanup()

            mock_browser.cancel.assert_called_once()
            assert api._global_browser is None

    async def test_cleanup_disconnects_all(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test cleanup disconnects all devices."""
        with patch.object(
            api._connection,
            "disconnect_all",
            new_callable=AsyncMock,
        ) as mock_disconnect:
            await api.cleanup()

            mock_disconnect.assert_called_once()


class TestESPWeaverApiServices:
    """Test ESPWeaverApi service methods."""

    async def test_start_services_already_started(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test start_services when already started."""
        api._global_browser = MagicMock()

        await api.start_services()

        # Should not have changed
        assert api._global_browser is not None

    async def test_start_services_enabled_creates_browser(
        self,
        host: FakeHost,
        api: ESPWeaverApi,
    ) -> None:
        """Test start_services with discovery enabled creates browser."""
        with patch(
            "custom_components.esp_weaver.iot.client.device_api.ServiceBrowser"
        ) as mock_browser_class:
            mock_browser = MagicMock()
            mock_browser_class.return_value = mock_browser

            await api.start_services()

            # Assert ServiceBrowser was instantiated with expected arguments
            mock_browser_class.assert_called_once()
            call_args = mock_browser_class.call_args
            # First arg should be the host's zeroconf instance
            assert call_args[0][0] is host.zc
            # Second arg should be the service type (MDNS_SERVICE_TYPE)
            assert "_esp_local_ctrl._tcp.local." in call_args[0][1]
            # Third arg should be the listener
            assert call_args[0][2] is api._global_listener
            # Browser should be assigned to api._global_browser
            assert api._global_browser is mock_browser

    async def test_start_services_survives_zeroconf_failing(
        self,
        host: FakeHost,
        api: ESPWeaverApi,
    ) -> None:
        """Test a node reached by its address still sets up without discovery."""
        host.zeroconf.side_effect = RuntimeError("test error")

        await api.start_services()

        assert api._global_browser is None


class TestESPWeaverApiReadProperties:
    """Reading a local node over the session the API holds for it."""

    async def test_a_node_without_a_session_reads_as_none(
        self, api: ESPWeaverApi
    ) -> None:
        """No session means nothing to ask, which is different from an empty answer."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        assert await api.read_properties(TEST_NODE_ID) is None

    async def test_the_session_is_asked(self, api: ESPWeaverApi) -> None:
        """The answer is the node's, passed through untouched."""
        client = MagicMock()
        client.get_property_values = AsyncMock(return_value=[{"name": "Light"}])
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
        api.registry.set_client(TEST_NODE_ID, client)

        assert await api.read_properties(TEST_NODE_ID) == [{"name": "Light"}]

    async def test_a_node_that_did_not_answer_reads_as_none(
        self, api: ESPWeaverApi
    ) -> None:
        """The client answers None for a node it could not ask; so does the API."""
        client = MagicMock()
        client.get_property_values = AsyncMock(return_value=None)
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
        api.registry.set_client(TEST_NODE_ID, client)

        assert await api.read_properties(TEST_NODE_ID) is None


class TestESPWeaverApiDeviceData:
    """Test ESPWeaverApi device data methods."""

    def test_get_device_data(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test get_device_data method."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        result = api.get_device_data(TEST_NODE_ID)

        assert result is not None
        assert result["ip"] == TEST_IP_ADDRESS

    def test_get_device_data_not_found(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test get_device_data for non-existent device."""
        result = api.get_device_data("non_existent")

        assert result is None

    def test_get_device_data_is_synchronous(self) -> None:
        """Diagnostics reads it straight; awaiting a dict is a 500 instead."""
        assert not inspect.iscoroutinefunction(ESPWeaverApi.get_device_data)

    def test_get_firmware_version(self, api: ESPWeaverApi) -> None:
        """The version parsed out of a node's config, for its update entity."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)
        device = api.registry.get_device(TEST_NODE_ID)
        assert device is not None
        device.firmware_version = "1.2.3"

        assert api.get_firmware_version(TEST_NODE_ID) == "1.2.3"

    def test_get_firmware_version_before_discovery(self, api: ESPWeaverApi) -> None:
        """An empty version is no version, so it does not become an empty string."""
        api.registry.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        assert api.get_firmware_version(TEST_NODE_ID) is None
        assert api.get_firmware_version("non_existent") is None


class TestESPWeaverApiInternalMethods:
    """Test ESPWeaverApi internal methods."""

    async def test_connect_and_sync_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _connect_and_sync_device method."""
        with (
            patch.object(
                api._connection,
                "connect_and_sync",
                new_callable=AsyncMock,
                return_value=(True, [{"name": "config", "value": b"{}"}]),
            ),
            patch.object(
                api._discovery,
                "parse_and_discover_entities",
                new_callable=AsyncMock,
            ),
        ):
            success, properties = await api._connect_and_sync_device(
                TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT
            )

            assert success is True
            assert properties is not None

    async def test_connect_and_sync_device_failure(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test _connect_and_sync_device when connection fails."""
        with patch.object(
            api._connection,
            "connect_and_sync",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            success, properties = await api._connect_and_sync_device(
                TEST_NODE_ID, TEST_IP_ADDRESS, TEST_PORT
            )

            assert success is False
            assert properties is None


class TestCloudControl:
    """Test cloud control methods."""

    async def test_get_cloud_params_no_client(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test get_cloud_params when no cloud client configured."""
        result = await api.get_cloud_params(TEST_NODE_ID)
        assert result is None

    async def test_get_cloud_params_success(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test get_cloud_params success."""
        mock_cloud_client = MagicMock()
        mock_cloud_client.get_params = AsyncMock(
            return_value={"Light": {"power": True}}
        )
        api.set_cloud_client(TEST_NODE_ID, mock_cloud_client)

        result = await api.get_cloud_params(TEST_NODE_ID)
        assert result == {"Light": {"power": True}}

    async def test_is_device_available_async_cloud_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_device_available_async for cloud device."""
        mock_cloud_client = MagicMock()
        mock_cloud_client.get_node_connectivity = AsyncMock(return_value=True)
        api.set_cloud_client(TEST_NODE_ID, mock_cloud_client)
        api.set_connection_mode(TEST_NODE_ID, "cloud")
        api.register_cloud_device(TEST_NODE_ID)
        result = await api.is_device_available_async(TEST_NODE_ID)
        assert result is True

    async def test_register_cloud_device(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_cloud_device."""
        api.set_connection_mode(TEST_NODE_ID, "cloud")
        api.register_cloud_device(TEST_NODE_ID)
        assert api.is_cloud_device(TEST_NODE_ID) is True

    async def test_is_cloud_device_not_registered(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test is_cloud_device for non-cloud device."""
        assert api.is_cloud_device(TEST_NODE_ID) is False

    async def test_discover_from_cloud(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test discover_from_cloud method."""
        with patch.object(
            api._discovery,
            "discover_from_cloud",
            new_callable=AsyncMock,
        ) as mock_discover:
            await api.discover_from_cloud(
                TEST_NODE_ID, {"devices": []}, {"Light": {"power": True}}
            )

            mock_discover.assert_called_once_with(
                TEST_NODE_ID, {"devices": []}, {"Light": {"power": True}}
            )

    async def test_register_device_connection_raises_exception(
        self,
        api: ESPWeaverApi,
    ) -> None:
        """Test register_device when connection raises exception."""
        with (
            patch.object(
                api,
                "_connect_and_sync_device",
                new_callable=AsyncMock,
                side_effect=OSError("Connection failed"),
            ),
            pytest.raises(OSError),
        ):
            await api.register_device(TEST_NODE_ID, TEST_IP_ADDRESS)

        # Device should be marked as not registered
        device = api.registry.get_device(TEST_NODE_ID)
        assert device is not None
        assert device.registered is False
