# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven button platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.button import ButtonDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.esp_weaver.button import ESPWeaverButton
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import (
    TEST_DEVICE_NAME,
    TEST_NODE_ID,
    record_state_writes,
    state_writes,
)

DEVICE = "System"
PARAM = "Reboot"


def _binding(**overrides: Any) -> EntityBinding:
    """Build a one-shot action binding."""
    spec = AttributeSpec(
        name="press",
        param=PARAM,
        data_type="bool",
        properties=("read", "write"),
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.service.system",
        "platform": "button",
        "name": "Reboot",
        "key": "reboot",
        "device_class": "restart",
        "entity_category": "config",
        "attributes": {
            "press": ParamBinding(device_name=DEVICE, attribute="press", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


class TestButton:
    """Pressing an action parameter."""

    def test_translates_the_device_class(self, coordinator: MagicMock) -> None:
        """A declared device_class reaches Home Assistant."""
        entity = ESPWeaverButton(coordinator, _binding(), TEST_DEVICE_NAME)

        assert entity.device_class == ButtonDeviceClass.RESTART

    async def test_press_writes_true(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Pressing asks the device to act, which RainMaker spells as true."""
        entity = ESPWeaverButton(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "button.test"

        await entity.async_press()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: True}}
        )

    def test_a_report_is_not_a_state(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The device echoing the parameter must not write entity state."""
        entity = ESPWeaverButton(coordinator, _binding(), TEST_DEVICE_NAME)
        entity.hass = hass
        entity.entity_id = "button.test"
        record_state_writes(entity)

        entity._handle_params_update({DEVICE: {PARAM: True}})

        state_writes(entity).assert_not_called()

    async def test_a_read_only_parameter_refuses_the_press(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A device that will not accept the write must not look pressed."""
        spec = AttributeSpec(name="press", param=PARAM, properties=("read",))
        entity = ESPWeaverButton(
            coordinator,
            _binding(
                attributes={
                    "press": ParamBinding(
                        device_name=DEVICE, attribute="press", spec=spec
                    )
                }
            ),
            TEST_DEVICE_NAME,
        )
        entity.hass = hass
        entity.entity_id = "button.test"

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_press()
        assert err.value.translation_key == "write_not_supported"

        coordinator.api.set_params.assert_not_called()
