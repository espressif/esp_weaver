# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the one-off read of a device that is not set up yet."""

from collections.abc import Generator
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esp_weaver.iot.client.node_reader import read_node
from custom_components.esp_weaver.iot.discovered_device import DiscoveredDevice

NODE_CONFIG: dict[str, Any] = {
    "node_id": "node123",
    "devices": [{"name": "Light", "type": "esp.device.lightbulb", "params": []}],
}
NODE_PARAMS: dict[str, Any] = {"Light": {"Power": True}}


def _property(name: str, value: dict[str, Any]) -> dict[str, Any]:
    """Return one property as the device answers it."""
    return {"name": name, "value": json.dumps(value).encode()}


@pytest.fixture
def device() -> DiscoveredDevice:
    """A device discovery listed, with its security settled and a PoP typed."""
    return DiscoveredDevice(
        ip="192.168.1.100",
        node_id="node123",
        port=8080,
        security_version=1,
        pop="abcd1234",
    )


@pytest.fixture
def client() -> MagicMock:
    """A client that reaches the device and answers its config and params."""
    client = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.get_property_values = AsyncMock(
        return_value=[
            _property("config", NODE_CONFIG),
            _property("params", NODE_PARAMS),
        ]
    )
    client.disconnect = AsyncMock()
    return client


@pytest.fixture
def client_class(client: MagicMock) -> Generator[MagicMock]:
    """Stand in for the client class the reader builds its client from."""
    with patch(
        "custom_components.esp_weaver.iot.client.node_reader.ESPLocalCtrlClient",
        return_value=client,
    ) as cls:
        yield cls


class TestReadNode:
    """Open a session, read once, close."""

    async def test_a_device_that_answers_gives_its_config_and_params(
        self, device: DiscoveredDevice, client: MagicMock, client_class: MagicMock
    ) -> None:
        """Both halves of the answer come back, parsed, in that order."""
        assert await read_node(device) == (NODE_CONFIG, NODE_PARAMS)

        client.connect.assert_awaited_once()
        client.get_property_values.assert_awaited_once()

    async def test_the_client_is_built_from_what_the_device_said(
        self, device: DiscoveredDevice, client_class: MagicMock
    ) -> None:
        """The address, port, PoP and security mode are the discovered device's."""
        await read_node(device)

        client_class.assert_called_once_with(
            "node123",
            "192.168.1.100",
            port=8080,
            pop="abcd1234",
            security_mode=1,
        )

    async def test_a_device_whose_security_is_unknown_is_read_open(
        self, device: DiscoveredDevice, client_class: MagicMock
    ) -> None:
        """Security not yet detected is read as none rather than left to fail."""
        device.security_version = None

        await read_node(device)

        assert client_class.call_args.kwargs["security_mode"] == 0

    async def test_a_device_that_cannot_be_reached_is_none(
        self, device: DiscoveredDevice, client: MagicMock, client_class: MagicMock
    ) -> None:
        """A failed connection is answered with None, and nothing is asked over it."""
        client.connect = AsyncMock(return_value=False)

        assert await read_node(device) is None

        client.get_property_values.assert_not_awaited()

    async def test_a_device_that_does_not_answer_is_none(
        self, device: DiscoveredDevice, client: MagicMock, client_class: MagicMock
    ) -> None:
        """The client's None for an unanswered read is the reader's None too."""
        client.get_property_values = AsyncMock(return_value=None)

        assert await read_node(device) is None

    async def test_a_device_that_answers_without_a_half_gives_an_empty_one(
        self, device: DiscoveredDevice, client: MagicMock, client_class: MagicMock
    ) -> None:
        """An answer missing its params is still an answer: {} for that half."""
        client.get_property_values = AsyncMock(
            return_value=[_property("config", NODE_CONFIG)]
        )

        assert await read_node(device) == (NODE_CONFIG, {})

    async def test_an_empty_answer_gives_two_empty_halves(
        self, device: DiscoveredDevice, client: MagicMock, client_class: MagicMock
    ) -> None:
        """A device that answered with no properties at all is not None."""
        client.get_property_values = AsyncMock(return_value=[])

        assert await read_node(device) == ({}, {})

    @pytest.mark.parametrize(
        "outcome",
        [
            pytest.param("answered", id="answered"),
            pytest.param("unanswered", id="unanswered"),
            pytest.param("unreachable", id="unreachable"),
        ],
    )
    async def test_the_session_is_always_closed(
        self,
        device: DiscoveredDevice,
        client: MagicMock,
        client_class: MagicMock,
        outcome: str,
    ) -> None:
        """Whatever the device did, the one-off session does not outlive the read."""
        if outcome == "unanswered":
            client.get_property_values = AsyncMock(return_value=None)
        elif outcome == "unreachable":
            client.connect = AsyncMock(return_value=False)

        await read_node(device)

        client.disconnect.assert_awaited_once()

    async def test_the_session_is_closed_even_when_the_read_raises(
        self, device: DiscoveredDevice, client: MagicMock, client_class: MagicMock
    ) -> None:
        """An error the client did not catch still leaves no session behind."""
        client.get_property_values = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await read_node(device)

        client.disconnect.assert_awaited_once()
