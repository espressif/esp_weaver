# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The shipped IotHost: what it writes back into Home Assistant."""

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esp_weaver.const import CONF_NODE_ID
from custom_components.esp_weaver.helpers.host import HomeAssistantHost
from custom_components.esp_weaver.identity import DOMAIN


def _listened(entry: MockConfigEntry) -> list[None]:
    """Attach an update listener to the entry and return what it records."""
    calls: list[None] = []

    async def _on_update(_hass: HomeAssistant, _entry: MockConfigEntry) -> None:
        calls.append(None)

    entry.add_update_listener(_on_update)
    return calls


def _entry(hass: HomeAssistant, node_id: str = "node-1") -> MockConfigEntry:
    """Add an entry for a local node at the address it was found at."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.10", CONF_PORT: 8080, CONF_NODE_ID: node_id},
        unique_id=node_id,
    )
    entry.add_to_hass(hass)
    return entry


class TestAddressSeen:
    """A node announcing itself from a new address updates its entry."""

    async def test_a_moved_node_has_its_entry_rewritten(
        self, hass: HomeAssistant
    ) -> None:
        """The next start connects to where the node was last seen."""
        entry = _entry(hass)
        listener_calls = _listened(entry)

        HomeAssistantHost(hass).address_seen("node-1", "192.168.1.77", 8081)
        await hass.async_block_till_done()

        assert entry.data[CONF_HOST] == "192.168.1.77"
        assert entry.data[CONF_PORT] == 8081
        # The rest of the entry is as it was.
        assert entry.data[CONF_NODE_ID] == "node-1"
        assert entry.options == {}
        # The entry's listener is told, as for any change to it.
        assert listener_calls

    async def test_the_same_address_writes_nothing(self, hass: HomeAssistant) -> None:
        """Every announcement passes through here; most say nothing new."""
        entry = _entry(hass)
        listener_calls = _listened(entry)

        HomeAssistantHost(hass).address_seen("node-1", "192.168.1.10", 8080)
        await hass.async_block_till_done()

        assert entry.data[CONF_HOST] == "192.168.1.10"
        assert listener_calls == []

    async def test_a_node_without_an_entry_is_ignored(
        self, hass: HomeAssistant
    ) -> None:
        """The listener only reports configured nodes, but nothing breaks if not."""
        entry = _entry(hass)

        HomeAssistantHost(hass).address_seen("other", "192.168.1.77", 8081)
        await hass.async_block_till_done()

        assert entry.data[CONF_HOST] == "192.168.1.10"
