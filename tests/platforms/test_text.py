# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven text platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.text import MAX_LENGTH, ESPWeaverText
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Time"
PARAM = "TZ"


def _binding(**overrides: Any) -> EntityBinding:
    """Build a writable string binding."""
    spec = AttributeSpec(
        name="value",
        param=PARAM,
        data_type="string",
        properties=("read", "write"),
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.service.time",
        "platform": "text",
        "name": "Timezone",
        "key": "timezone",
        "entity_category": "config",
        "attributes": {
            "value": ParamBinding(device_name=DEVICE, attribute="value", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


class TestTextReads:
    """Adopting the string a device reports."""

    def test_starts_without_a_value(self, coordinator: MagicMock) -> None:
        """Nothing is invented before the device has reported."""
        entity = ESPWeaverText(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.native_value is None
        assert entity.entity_category == EntityCategory.CONFIG

    def test_adopts_the_reported_string(self, coordinator: MagicMock) -> None:
        """The report a node already published becomes the value."""
        entity = ESPWeaverText(
            coordinator, _binding(), TEST_DEVICE_NAME, report={PARAM: "Asia/Shanghai"}
        )

        assert entity.native_value == "Asia/Shanghai"

    def test_truncates_an_over_long_string(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Home Assistant rejects a value past native_max, so it is cut."""
        entity = ESPWeaverText(
            coordinator,
            _binding(),
            TEST_DEVICE_NAME,
            report={PARAM: "x" * (MAX_LENGTH + 10)},
        )

        assert entity.native_value == "x" * MAX_LENGTH
        assert "keeping the first" in caplog.text


class TestTextWrites:
    """Sending a new string."""

    async def test_set_value_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The value reaches the parameter the binding names."""
        entity = ESPWeaverText(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "text.test"

        await entity.async_set_value("Europe/Berlin")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: "Europe/Berlin"}}
        )
        assert entity.native_value == "Europe/Berlin"
