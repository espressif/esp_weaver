# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test what one poll of a node consists of, for each way a node is reached."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.esp_weaver.const import (
    CLOUD_CONNECTIVITY_CHECK_POLLS,
    CLOUD_DISCOVERY_FAILURE_THRESHOLD,
    CLOUD_FIRMWARE_CHECK_POLLS,
    CLOUD_PARAMS_FAILURE_THRESHOLD,
)
from custom_components.esp_weaver.polling import CloudPoller, LocalPoller

from .conftest import TEST_NODE_ID


@pytest.fixture
def local(mock_esp_api: MagicMock) -> LocalPoller:
    """A poller for the test node over local control."""
    return LocalPoller(mock_esp_api, TEST_NODE_ID)


@pytest.fixture
def cloud(mock_esp_api: MagicMock) -> CloudPoller:
    """A poller for the test node through the cloud."""
    return CloudPoller(mock_esp_api, TEST_NODE_ID)


class TestLocalDiscovery:
    """Discovering a local node."""

    @pytest.mark.parametrize("properties", [None, []])
    async def test_a_node_that_did_not_answer_is_not_discovered(
        self, local: LocalPoller, mock_esp_api: MagicMock, properties: list | None
    ) -> None:
        """Nothing read means nothing to discover from."""
        mock_esp_api.read_properties = AsyncMock(return_value=properties)

        await local.discover()

        mock_esp_api.parse_and_discover_entities.assert_not_called()
        mock_esp_api.mark_discovery_completed.assert_not_called()

    async def test_what_was_read_is_discovered_from(
        self, local: LocalPoller, mock_esp_api: MagicMock
    ) -> None:
        """A successful read is handed to discovery and marked complete."""
        mock_esp_api.read_properties = AsyncMock(return_value=[{"name": "test"}])

        await local.discover()

        mock_esp_api.parse_and_discover_entities.assert_called_once_with(
            TEST_NODE_ID, [{"name": "test"}]
        )
        mock_esp_api.mark_discovery_completed.assert_called_once_with(TEST_NODE_ID)


class TestLocalPoll:
    """Reading a local node's parameters back, poll after poll."""

    async def test_a_report_is_published(
        self, local: LocalPoller, mock_esp_api: MagicMock
    ) -> None:
        """The read goes out by the one route every report takes."""
        params = {"Light": {"Power": True}}
        mock_esp_api.read_properties = AsyncMock(return_value=[])

        with patch(
            "custom_components.esp_weaver.polling.get_raw_params",
            return_value=params,
        ):
            assert await local.poll() is True

        mock_esp_api.publish_params.assert_awaited_once_with(TEST_NODE_ID, params)

    async def test_a_poll_that_read_nothing_publishes_nothing(
        self, local: LocalPoller, mock_esp_api: MagicMock
    ) -> None:
        """An empty answer is still an answer, but there is nothing to act on."""
        mock_esp_api.read_properties = AsyncMock(return_value=[])

        assert await local.poll() is True

        mock_esp_api.publish_params.assert_not_awaited()

    async def test_a_node_that_could_not_be_asked_says_so(
        self, local: LocalPoller, mock_esp_api: MagicMock
    ) -> None:
        """The session can be gone by the time the poll runs; the poll answers False."""
        mock_esp_api.read_properties = AsyncMock(return_value=None)

        assert await local.poll() is False
        mock_esp_api.publish_params.assert_not_awaited()

    async def test_the_version_in_the_read_is_taken(
        self, local: LocalPoller, mock_esp_api: MagicMock
    ) -> None:
        """The config comes back with every read, so an OTA is noticed."""
        mock_esp_api.read_properties = AsyncMock(return_value=[])

        with patch(
            "custom_components.esp_weaver.polling.firmware_version_from_properties",
            return_value="2.0.0",
        ):
            await local.poll()

        mock_esp_api.set_firmware_version.assert_called_once_with(TEST_NODE_ID, "2.0.0")

    async def test_reachability_is_the_session(
        self, local: LocalPoller, mock_esp_api: MagicMock
    ) -> None:
        """A local node is reachable while the API holds a live session to it."""
        mock_esp_api.is_device_available_async = AsyncMock(return_value=False)

        assert await local.is_reachable() is False
        assert local.reconnects is True


class TestCloudDiscovery:
    """Discovering a cloud node."""

    async def test_config_and_params_are_discovered_from(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """The node's config and current params are handed to discovery."""
        node_config = {"devices": [{"name": "Light", "type": "esp.device.light"}]}
        cloud_params = {"Light": {"power": True}}
        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=node_config)
        mock_esp_api.get_cloud_params = AsyncMock(return_value=cloud_params)
        mock_esp_api.discover_from_cloud = AsyncMock()

        await cloud.discover()

        mock_esp_api.discover_from_cloud.assert_called_once_with(
            TEST_NODE_ID, node_config, cloud_params
        )
        mock_esp_api.mark_discovery_completed.assert_called_once_with(TEST_NODE_ID)

    @pytest.mark.parametrize("node_config", [None, {"info": {}}])
    async def test_a_config_without_devices_is_not_discovered_from(
        self, cloud: CloudPoller, mock_esp_api: MagicMock, node_config: dict | None
    ) -> None:
        """The cloud has not stored the node's config yet."""
        mock_esp_api.get_cloud_node_config = AsyncMock(return_value=node_config)
        mock_esp_api.discover_from_cloud = AsyncMock()

        await cloud.discover()

        mock_esp_api.discover_from_cloud.assert_not_called()

    async def test_waiting_for_the_config_eventually_fails(
        self, cloud: CloudPoller
    ) -> None:
        """A new node's config arrives late; one that never arrives is a fault."""
        for _ in range(CLOUD_DISCOVERY_FAILURE_THRESHOLD - 1):
            cloud.discovery_pending()

        with pytest.raises(UpdateFailed):
            cloud.discovery_pending()

    async def test_a_successful_poll_forgives_the_wait(
        self, cloud: CloudPoller
    ) -> None:
        """Once the node has been read, the count starts over."""
        for _ in range(CLOUD_DISCOVERY_FAILURE_THRESHOLD - 1):
            cloud.discovery_pending()

        cloud.polled_ok()
        cloud.discovery_pending()

    async def test_the_cloud_is_always_reachable(self, cloud: CloudPoller) -> None:
        """Whether the node is online is the cloud's answer, not a precondition."""
        assert await cloud.is_reachable() is True
        assert cloud.reconnects is False


class TestCloudPoll:
    """Polling a cloud node's state."""

    async def test_params_are_published(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """Test successful cloud polling with params."""
        params = {"Light": {"power": True}}
        mock_esp_api.get_cloud_params = AsyncMock(return_value=params)

        assert await cloud.poll() is True

        mock_esp_api.publish_params.assert_awaited_once_with(TEST_NODE_ID, params)
        assert cloud._params_failures == 0

    async def test_the_config_is_left_alone_between_checks(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """Test an ordinary poll costs no more cloud calls than it used to."""
        mock_esp_api.get_cloud_params = AsyncMock(return_value={"Light": {"on": True}})
        mock_esp_api.get_cloud_node_config = AsyncMock()

        await cloud.poll()

        mock_esp_api.get_cloud_node_config.assert_not_called()

    async def test_the_version_is_taken_on_the_check_poll(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """Test the version a node has taken on by OTA is eventually adopted."""
        mock_esp_api.get_cloud_params = AsyncMock(return_value={"Light": {"on": True}})
        mock_esp_api.get_cloud_node_config = AsyncMock(
            return_value={"info": {"fw_version": "1.0.2"}}
        )
        cloud._firmware_poll_count = CLOUD_FIRMWARE_CHECK_POLLS - 1

        await cloud.poll()

        mock_esp_api.set_firmware_version.assert_called_once_with(TEST_NODE_ID, "1.0.2")
        assert cloud._firmware_poll_count == 0

    async def test_an_unreadable_config_does_not_cost_the_poll(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """The entities have their params; a stale version is not a failed poll."""
        mock_esp_api.get_cloud_params = AsyncMock(return_value={"Light": {"on": True}})
        mock_esp_api.get_cloud_node_config = AsyncMock(side_effect=TimeoutError)
        cloud._firmware_poll_count = CLOUD_FIRMWARE_CHECK_POLLS - 1

        assert await cloud.poll() is True

        mock_esp_api.set_firmware_version.assert_not_called()

    async def test_missing_params_are_tolerated_up_to_a_point(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """A few empty answers are the cloud's latency; a run of them is the node."""
        mock_esp_api.get_cloud_params = AsyncMock(return_value=None)

        for _ in range(CLOUD_PARAMS_FAILURE_THRESHOLD - 1):
            assert await cloud.poll() is True

        assert await cloud.poll() is False

    async def test_an_offline_node_is_asked_about_on_every_poll(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """Test poll returns False when device is marked offline."""
        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=False)
        mock_esp_api.get_cloud_params = AsyncMock()
        cloud._offline = True

        assert await cloud.poll() is False
        mock_esp_api.get_cloud_params.assert_not_awaited()

    async def test_connectivity_is_checked_every_few_polls(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """Test connectivity check triggered at poll interval."""
        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=False)
        cloud._poll_count = CLOUD_CONNECTIVITY_CHECK_POLLS - 1

        assert await cloud.poll() is False
        assert cloud._poll_count == 0


class TestCloudConnectivity:
    """What the cloud's connectivity answer does to the node's standing."""

    async def test_a_node_that_came_back(
        self, cloud: CloudPoller, mock_esp_api: MagicMock
    ) -> None:
        """Coming back online also forgives the params it failed to give meanwhile."""
        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=True)
        cloud._offline = True
        cloud._params_failures = 2

        assert await cloud._check_connectivity() is True
        assert cloud._offline is False
        assert cloud._params_failures == 0

    @pytest.mark.parametrize("answer", [False, None])
    async def test_a_node_that_is_not_there(
        self, cloud: CloudPoller, mock_esp_api: MagicMock, answer: bool | None
    ) -> None:
        """Offline, or unknown to the account: either way not to be polled."""
        mock_esp_api.is_cloud_device_online = AsyncMock(return_value=answer)

        assert await cloud._check_connectivity() is False
        assert cloud._offline is True
