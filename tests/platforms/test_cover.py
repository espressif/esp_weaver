# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven cover platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant

from custom_components.esp_weaver.cover import ESPWeaverCover
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Blinds"
PARAM = "Blinds Position"


def _binding(**overrides: Any) -> EntityBinding:
    """Build a cover binding driven by a position parameter."""
    spec = AttributeSpec(
        name="position",
        param=PARAM,
        data_type="int",
        properties=("read", "write"),
        bounds={"min": 0, "max": 100, "step": 1},
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.blinds-internal",
        "platform": "cover",
        "name": "Internal Blinds",
        "key": "internal_blinds",
        "device_class": "blind",
        "attributes": {
            "position": ParamBinding(
                device_name=DEVICE, attribute="position", spec=spec
            )
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
) -> ESPWeaverCover:
    entity = ESPWeaverCover(coordinator, _binding(), TEST_DEVICE_NAME, report=report)
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "cover.test"
    return entity


class TestCoverCapabilities:
    """What the card offers follows what the firmware said it can do."""

    def test_a_writable_position_gets_the_controls(
        self, coordinator: MagicMock
    ) -> None:
        """The ordinary blind, which takes a position."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
        )

    def test_a_read_only_position_gets_no_buttons(self, coordinator: MagicMock) -> None:
        """Buttons that write nothing would move the card and not the blind."""
        spec = AttributeSpec(
            name="position",
            param=PARAM,
            data_type="int",
            properties=("read",),
            bounds={"min": 0, "max": 100},
        )
        binding = _binding(
            attributes={
                "position": ParamBinding(
                    device_name=DEVICE, attribute="position", spec=spec
                )
            }
        )
        entity = ESPWeaverCover(coordinator, binding, TEST_DEVICE_NAME)

        assert entity.supported_features == CoverEntityFeature(0)


class TestCoverReads:
    """Turning a position into what Home Assistant shows."""

    def test_starts_unknown(self, coordinator: MagicMock) -> None:
        """A cover of unknown position is not reported as closed."""
        entity = _entity(coordinator)

        assert entity.current_cover_position is None
        assert entity.is_closed is None

    def test_reports_a_partial_position(self, coordinator: MagicMock) -> None:
        """A cover part way along its travel is open, not closed."""
        entity = _entity(coordinator, report={PARAM: 40})

        assert entity.current_cover_position == 40
        assert entity.is_closed is False

    def test_zero_is_closed(self, coordinator: MagicMock) -> None:
        """The bottom of the travel is the closed end."""
        entity = _entity(coordinator, report={PARAM: 0})

        assert entity.is_closed is True

    def test_offers_position_control(self, coordinator: MagicMock) -> None:
        """A position parameter is enough for all three controls."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
        )
        assert entity.device_class == CoverDeviceClass.BLIND


class TestCoverWrites:
    """Moving the cover."""

    async def test_open_goes_to_the_top(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Opening is a write to the open end of the travel."""
        entity = _entity(coordinator, hass)

        await entity.async_open_cover()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: 100}}
        )

    async def test_close_goes_to_the_bottom(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Closing is a write to the closed end of the travel."""
        entity = _entity(coordinator, hass)

        await entity.async_close_cover()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: 0}}
        )
        assert entity.is_closed is True

    async def test_set_position_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A requested position is passed through as given."""
        entity = _entity(coordinator, hass)

        await entity.async_set_cover_position(**{ATTR_POSITION: 30})

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {PARAM: 30}}
        )
        assert entity.current_cover_position == 30

    async def test_a_call_without_a_position_writes_nothing(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Nothing to move to is nothing to send."""
        entity = _entity(coordinator, hass)

        await entity.async_set_cover_position()

        coordinator.api.set_params.assert_not_called()
