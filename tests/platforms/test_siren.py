# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven siren platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.siren import SirenEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from custom_components.esp_weaver.siren import ESPWeaverSiren
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Siren"
POWER = "Power"


def _binding(**overrides: Any) -> EntityBinding:
    """Build a siren binding driven by one power parameter."""
    spec = AttributeSpec(
        name="state",
        param=POWER,
        data_type="bool",
        properties=("read", "write"),
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.siren",
        "platform": "siren",
        "name": "Siren",
        "key": "siren",
        "attributes": {
            "state": ParamBinding(device_name=DEVICE, attribute="state", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverSiren:
    entity = ESPWeaverSiren(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "siren.test"
    return entity


class TestSirenCapabilities:
    """What the siren offers."""

    def test_offers_only_sounding_and_silencing(self, coordinator: MagicMock) -> None:
        """Tones and durations have no parameter behind them."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
        )


class TestSirenReads:
    """Turning a report into a state."""

    def test_state_is_unknown_before_the_device_speaks(
        self, coordinator: MagicMock
    ) -> None:
        """Silence is not the same as nothing said."""
        entity = _entity(coordinator)

        assert entity.is_on is None

    def test_adopts_the_reported_state(self, coordinator: MagicMock) -> None:
        """A sounding siren reads as on."""
        entity = _entity(coordinator, report={POWER: True})

        assert entity.is_on is True


class TestSirenWrites:
    """Sounding and silencing the siren."""

    async def test_turn_on_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Sounding is a write to the power parameter."""
        entity = _entity(coordinator, hass)

        await entity.async_turn_on()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )
        assert entity.is_on is True

    async def test_turn_off_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Silencing is the same write the other way."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.is_on is False

    async def test_extra_service_data_is_ignored(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A tone asked for by an automation must not break the write."""
        entity = _entity(coordinator, hass)

        await entity.async_turn_on(tone="chime", duration=5)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: True}}
        )
