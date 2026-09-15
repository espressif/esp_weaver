# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver event entity, driven entirely by device profiles."""

from collections.abc import Mapping
import logging
from typing import Any, Final

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.const import Platform

from .entity import ESPWeaverBindingEntity
from .helpers.discovery import binding_platform_setup

_LOGGER = logging.getLogger(__name__)

# Read-only: the coordinator alone updates these entities.
PARALLEL_UPDATES: Final = 0

# Profile attribute name this platform understands.
ATTR_EVENT_TYPE: Final = "event_type"


class ESPWeaverEvent(ESPWeaverBindingEntity, EventEntity):
    """A stream of one-off happenings, such as the presses of a wireless button.

    RainMaker has no events: a device leaves what happened in a parameter, so
    an event is a change of that value, and the firmware clearing it lets the
    same press count twice in a row.
    """

    _device_class_enum = EventDeviceClass

    def _configure(self) -> None:
        """Set up the event entity."""
        binding = self._binding
        self._attr_event_types = list(binding.options or ())
        self._reported: str | None = None

        # What the device holds at startup already happened; remember it, but
        # do not fire it.
        self._seeded = False

    def _seed(self, report: Mapping[str, Any] | None) -> None:
        """Remember the held value without announcing it."""
        super()._seed(report)
        self._seeded = True

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Fire an event when the device reports something new."""
        if not self._mentions(ATTR_EVENT_TYPE, reported):
            # A report leaving the parameter out is not a clear, or the held
            # value would fire again on the next read.
            return False

        value = self._read(ATTR_EVENT_TYPE, reported)
        if not isinstance(value, str) or not value:
            # Cleared: the next report is a fresh happening, whatever it says.
            self._reported = None
            return False

        if value == self._reported:
            return False
        self._reported = value

        if not self._seeded:
            return False

        if value not in self.event_types:
            _LOGGER.warning(
                "Device %s reported %r for %s, which is not one of its event types",
                self._node_id,
                value,
                self._binding.name,
            )
            return False

        self._trigger_event(value)
        return True


async_setup_entry = binding_platform_setup(Platform.EVENT, ESPWeaverEvent)
