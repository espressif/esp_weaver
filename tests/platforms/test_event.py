# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven event platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.event import ATTR_EVENT_TYPE, EventDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import PlatformData
import pytest

from custom_components.esp_weaver.event import ESPWeaverEvent
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "Button"
PARAM = "Event"

EVENT_TYPES = ("single_press", "double_press", "long_press")


def _binding(**overrides: Any) -> EntityBinding:
    """Build an event binding whose types the profile fixes in advance."""
    spec = AttributeSpec(
        name="event_type",
        param=PARAM,
        data_type="string",
        properties=("read",),
        options=EVENT_TYPES,
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.event",
        "platform": "event",
        "name": "Event",
        "key": "event",
        "device_class": "button",
        "options": EVENT_TYPES,
        "attributes": {
            "event_type": ParamBinding(
                device_name=DEVICE, attribute="event_type", spec=spec
            )
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverEvent:
    entity = ESPWeaverEvent(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "event.test"
    return entity


def _fired(entity: ESPWeaverEvent) -> str | None:
    """Return the event type the entity last announced, if any."""
    event_type: str | None = entity.state_attributes[ATTR_EVENT_TYPE]
    return event_type


class TestEventCapabilities:
    """What the entity says it can announce."""

    def test_types_come_from_the_profile(self, coordinator: MagicMock) -> None:
        """Home Assistant needs the full set before the first event arrives."""
        entity = _entity(coordinator)

        assert entity.event_types == list(EVENT_TYPES)
        assert entity.device_class == EventDeviceClass.BUTTON


class TestEventNaming:
    """Which name the entity ends up with.

    The event platform is one of the few that names an unnamed entity after its
    device class, so it is where a profile name being ignored shows up first.
    """

    @staticmethod
    def _on_a_platform(
        hass: HomeAssistant, entity: ESPWeaverEvent, **translations: str
    ) -> ESPWeaverEvent:
        """Give the entity the platform context naming is resolved against."""
        data = PlatformData(hass, domain="event", platform_name="esp_weaver")
        data.platform_translations = dict(translations)
        entity.platform_data = data
        return entity

    def test_the_profile_name_is_used_when_untranslated(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Otherwise every event entity would be called "Button"."""
        entity = self._on_a_platform(hass, _entity(coordinator))

        assert entity.name == "Event"

    def test_a_translation_wins(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A profile key with a translation is named in the user's language."""
        entity = self._on_a_platform(
            hass,
            _entity(coordinator),
            **{"component.esp_weaver.entity.event.event.name": "Knopf"},
        )

        assert entity.name == "Knopf"


class TestEventFiring:
    """Which reports count as something having happened."""

    def test_a_new_value_fires(self, coordinator: MagicMock) -> None:
        """A press the device just announced is a press."""
        entity = _entity(coordinator)

        assert entity._apply_report({PARAM: "single_press"}) is True
        assert _fired(entity) == "single_press"

    def test_the_value_found_at_startup_does_not_fire(
        self, coordinator: MagicMock
    ) -> None:
        """A parameter left over from yesterday is not a press made today."""
        entity = _entity(coordinator, report={PARAM: "single_press"})

        assert _fired(entity) is None
        assert entity.state is None

    def test_the_same_value_repeated_fires_once(self, coordinator: MagicMock) -> None:
        """Every poll re-reads the parameter, and that is not a second press."""
        entity = _entity(coordinator)
        entity._apply_report({PARAM: "long_press"})

        assert entity._apply_report({PARAM: "long_press"}) is False

    def test_a_cleared_value_lets_the_same_press_count_again(
        self, coordinator: MagicMock
    ) -> None:
        """Firmware clearing the parameter is what separates two presses."""
        entity = _entity(coordinator)
        entity._apply_report({PARAM: "single_press"})
        entity._apply_report({PARAM: ""})

        assert entity._apply_report({PARAM: "single_press"}) is True

    def test_a_different_value_fires_without_clearing(
        self, coordinator: MagicMock
    ) -> None:
        """Firmware that never clears still gets a change out of a new press."""
        entity = _entity(coordinator)
        entity._apply_report({PARAM: "single_press"})

        assert entity._apply_report({PARAM: "double_press"}) is True
        assert _fired(entity) == "double_press"

    def test_a_report_without_the_parameter_is_ignored(
        self, coordinator: MagicMock
    ) -> None:
        """Other parameters of the same device report on their own schedule."""
        entity = _entity(coordinator)

        assert entity._apply_report({"Other": 1}) is False

    def test_a_report_without_the_parameter_is_not_a_clear(
        self, coordinator: MagicMock
    ) -> None:
        """Saying nothing about the parameter is not the same as clearing it.

        The device is still holding the press it announced, so reading it again
        after such a report must not count as a second one.
        """
        entity = _entity(coordinator)
        entity._apply_report({PARAM: "single_press"})

        assert entity._apply_report({"Other": 1}) is False
        assert entity._apply_report({PARAM: "single_press"}) is False

    def test_an_unlisted_value_is_refused(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An event type Home Assistant never heard of would raise on trigger."""
        entity = _entity(coordinator)

        assert entity._apply_report({PARAM: "quadruple_press"}) is False
        assert "not one of its event types" in caplog.text
