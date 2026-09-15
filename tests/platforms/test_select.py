# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven select platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.select import ESPWeaverSelect
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Washer"
PARAM = "Mode"
OPTIONS = ("Cotton", "Wool", "Quick")


def _binding(**overrides: Any) -> EntityBinding:
    """Build a writable enumerated binding."""
    spec = AttributeSpec(
        name="state",
        param=PARAM,
        data_type="string",
        properties=("read", "write"),
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.washer",
        "platform": "select",
        "name": "Mode",
        "key": "mode",
        "options": OPTIONS,
        "attributes": {
            "state": ParamBinding(device_name=DEVICE, attribute="state", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


class TestSelectReads:
    """Turning a reported enum value into a selected option."""

    def test_offers_the_resolved_options(self, coordinator: MagicMock) -> None:
        """The option list is the one resolution settled on."""
        entity = ESPWeaverSelect(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.options == list(OPTIONS)
        assert entity.current_option is None

    def test_adopts_the_reported_option(self, coordinator: MagicMock) -> None:
        """A reported value that is on offer becomes the selection."""
        entity = ESPWeaverSelect(
            coordinator, _binding(), TEST_DEVICE_NAME, report={PARAM: "Wool"}
        )

        assert entity.current_option == "Wool"

    def test_rejects_a_value_it_does_not_offer(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Home Assistant refuses a state outside options, so it is dropped."""
        entity = ESPWeaverSelect(
            coordinator, _binding(), TEST_DEVICE_NAME, report={PARAM: "Silk"}
        )

        assert entity.current_option is None
        assert "not one of" in caplog.text

    def test_keeps_the_selection_when_options_are_unknown(
        self, coordinator: MagicMock
    ) -> None:
        """With nothing enumerated, whatever the device says is the state."""
        entity = ESPWeaverSelect(
            coordinator,
            _binding(options=None),
            TEST_DEVICE_NAME,
            report={PARAM: "Silk"},
        )

        assert entity.current_option == "Silk"


class TestSelectWrites:
    """Choosing an option."""

    async def test_select_option_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The chosen option reaches the device it belongs to."""
        entity = ESPWeaverSelect(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "select.test"

        await entity.async_select_option("Quick")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: "Quick"}}
        )
        assert entity.current_option == "Quick"
