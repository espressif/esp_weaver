# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver event dispatcher module."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.esp_weaver.helpers.event_dispatcher import (
    create_event_dispatcher,
    fire_property_events,
)
from custom_components.esp_weaver.helpers.signals import signal_params_update
from tests.conftest import TEST_NODE_ID


def _listen(hass: HomeAssistant, node_id: str) -> list[dict[str, Any]]:
    """Collect every report dispatched for one node."""
    reports: list[dict[str, Any]] = []
    async_dispatcher_connect(hass, signal_params_update(node_id), reports.append)
    return reports


class TestCreateEventDispatcher:
    """Test create_event_dispatcher function."""

    async def test_creates_callable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that it creates a callable."""
        dispatcher = create_event_dispatcher(hass)

        assert callable(dispatcher)

    async def test_dispatcher_can_be_called(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that dispatcher can be called and delivers the report."""
        dispatcher = create_event_dispatcher(hass)
        reports = _listen(hass, TEST_NODE_ID)

        params = {"Light": {"Power": True}}
        dispatcher(TEST_NODE_ID, params)
        await hass.async_block_till_done()

        assert reports == [params]


class TestRawParamsDispatch:
    """Test the untouched report forwarded to binding-driven entities."""

    async def test_forwards_the_report_unchanged(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Device and parameter names survive so a binding can match on them."""
        reports = _listen(hass, TEST_NODE_ID)

        params = {"Outlet": {"Power": True}}
        fire_property_events(hass, TEST_NODE_ID, params)
        await hass.async_block_till_done()

        assert reports == [params]

    async def test_an_empty_report_fires_nothing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Nothing to report means no signal."""
        reports = _listen(hass, TEST_NODE_ID)

        fire_property_events(hass, TEST_NODE_ID, {})
        await hass.async_block_till_done()

        assert reports == []

    async def test_only_the_reporting_node_hears_it(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The signal is scoped to the node, so other nodes' entities stay quiet."""
        mine = _listen(hass, TEST_NODE_ID)
        theirs = _listen(hass, "other-node")

        fire_property_events(hass, TEST_NODE_ID, {"Outlet": {"Power": True}})
        await hass.async_block_till_done()

        assert len(mine) == 1
        assert theirs == []

    async def test_nothing_reaches_the_event_bus(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Reports are internal: the recorder must never see them."""
        seen: list[Any] = []
        hass.bus.async_listen("esp_weaver_params_update", seen.append)
        hass.bus.async_listen(f"esp_weaver_params_update_{TEST_NODE_ID}", seen.append)

        fire_property_events(hass, TEST_NODE_ID, {"Outlet": {"Power": True}})
        await hass.async_block_till_done()

        assert seen == []


class TestFirePropertyEvents:
    """Test fire_property_events function."""

    async def test_empty_params(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test with empty params."""
        # Should not raise
        fire_property_events(hass, TEST_NODE_ID, {})
        await hass.async_block_till_done()
