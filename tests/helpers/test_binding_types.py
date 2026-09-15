# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for turning a profile's declared strings into Home Assistant enums."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory
import pytest

from custom_components.esp_weaver.helpers.binding_types import (
    device_class_of,
    enum_member,
    state_class_of,
)
from custom_components.esp_weaver.profiles.binding import EntityBinding
from tests.conftest import TEST_NODE_ID


def _binding(**overrides: Any) -> EntityBinding:
    """Build a binding with only what these helpers read."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": "Sensor",
        "device_type": "esp.device.sensor",
        "platform": "sensor",
        "name": "Temperature",
        "key": "temperature",
        "attributes": {},
    }
    return EntityBinding(**{**defaults, **overrides})


class TestEnumMember:
    """One declared string against one enum."""

    def test_a_valid_value_is_the_member(self) -> None:
        """The plain case."""
        binding = _binding()

        assert (
            enum_member(binding, "diagnostic", EntityCategory)
            is EntityCategory.DIAGNOSTIC
        )

    def test_nothing_declared_is_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A profile that says nothing is not a profile with a mistake."""
        caplog.set_level(logging.WARNING)

        assert enum_member(_binding(), None, EntityCategory) is None

        assert caplog.text == ""

    def test_an_unknown_value_is_dropped_and_named(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A typo in YAML must not take the entity down with it."""
        caplog.set_level(logging.WARNING)

        assert enum_member(_binding(), "diagnostics", EntityCategory) is None

        assert "'Temperature' declares 'diagnostics'" in caplog.text
        assert "not a valid EntityCategory" in caplog.text


class TestClassLookups:
    """The two fields every platform reads through these."""

    def test_device_class_of_reads_the_binding(self) -> None:
        """The device class comes from the binding, in the platform's enum."""
        binding = _binding(device_class="temperature")

        assert device_class_of(binding, SensorDeviceClass) is (
            SensorDeviceClass.TEMPERATURE
        )

    def test_state_class_of_reads_the_binding(self) -> None:
        """So does the state class."""
        binding = _binding(state_class="measurement")

        assert state_class_of(binding, SensorStateClass) is (
            SensorStateClass.MEASUREMENT
        )

    def test_a_class_for_another_platform_is_not_this_one(self) -> None:
        """A sensor class is not a state class, whatever the profile says."""
        binding = _binding(state_class="temperature")

        assert state_class_of(binding, SensorStateClass) is None
