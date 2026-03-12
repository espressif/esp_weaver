# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Gesture utility functions."""

import contextlib
from dataclasses import dataclass, field
import logging
from typing import Any

from ..specs.gesture_specs import (
    DEFAULT_GESTURE_ICON,
    GESTURE_CIRCLE,
    GESTURE_CLAP_DOUBLE,
    GESTURE_CLAP_SINGLE,
    GESTURE_CLAP_TRIPLE,
    GESTURE_FLIP,
    GESTURE_ICONS,
    GESTURE_IDLE,
    GESTURE_PUSH,
    GESTURE_ROTATION,
    GESTURE_SHAKE,
    GESTURE_TOSS,
)
from ..specs.keys import (
    KEY_GESTURE_CONFIDENCE,
    KEY_GESTURE_DISPLAY_DURATION,
    KEY_GESTURE_TYPE,
    KEY_ORIENTATION_CHANGE,
    KEY_ORIENTATION_CHANGE_SHORT,
    KEY_ORIENTATION_X,
    KEY_ORIENTATION_Y,
    KEY_ORIENTATION_Z,
    KEY_POWER,
    KEY_SENSITIVITY,
    KEY_X_ORIENTATION,
    KEY_Y_ORIENTATION,
    KEY_Z_ORIENTATION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class GestureConfig:
    """Configuration for a gesture type."""

    event_attr: str | None
    display_name: str


@dataclass
class GestureUpdateResult:
    """Result of processing a gesture update.

    Optional attributes (confidence, power, sensitivity) default to None,
    indicating the device doesn't report that parameter.
    """

    gesture: str = GESTURE_IDLE
    gesture_triggered: bool = False
    events: dict[str, bool] = field(default_factory=dict)
    orientation: dict[str, Any] = field(default_factory=dict)
    display_duration: float = 2.0
    # Optional features: None means device doesn't report this parameter
    confidence: int | None = None
    power: bool | None = None
    sensitivity: int | None = None


class GestureProcessor:
    """Processor for IMU gesture sensor data.

    Handles gesture type normalization, event extraction, detection,
    and attribute building for Home Assistant entities.
    """

    # Expose GESTURE_IDLE for use by entities
    IDLE_STATE: str = GESTURE_IDLE

    GESTURE_CONFIGS: dict[str, GestureConfig] = {
        GESTURE_SHAKE: GestureConfig("shake_event", "Shake"),
        GESTURE_PUSH: GestureConfig("push_event", "Push"),
        GESTURE_CIRCLE: GestureConfig("circle_event", "Circle"),
        GESTURE_FLIP: GestureConfig("flip_event", "Flip"),
        GESTURE_TOSS: GestureConfig("toss_event", "Toss"),
        GESTURE_ROTATION: GestureConfig("rotation_event", "Rotation"),
        GESTURE_CLAP_SINGLE: GestureConfig("clap_single_event", "Single Clap"),
        GESTURE_CLAP_DOUBLE: GestureConfig("clap_double_event", "Double Clap"),
        GESTURE_CLAP_TRIPLE: GestureConfig("clap_triple_event", "Triple Clap"),
        GESTURE_IDLE: GestureConfig(None, "Idle"),
    }

    def normalize_gesture(self, gesture_type: str | None) -> str:
        """Normalize gesture type to standard format.

        Unknown gesture types are preserved as-is so that custom gestures
        defined in firmware are passed through to the entity layer.
        """
        if gesture_type is None:
            return GESTURE_IDLE
        gesture = str(gesture_type).lower().strip()
        if gesture in ("none", ""):
            return GESTURE_IDLE
        return gesture

    def get_event_attr(self, gesture_type: str) -> str | None:
        """Get event attribute name for gesture type.

        For known gestures, returns the registered event_attr.
        For unknown (custom) gestures, auto-derives as ``{gesture_type}_event``.
        """
        config = self.GESTURE_CONFIGS.get(gesture_type)
        if config:
            return config.event_attr
        return f"{gesture_type}_event"

    def parse_event_value(self, value: Any) -> bool:
        """Parse gesture event value to boolean."""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return False

    def parse_confidence(self, value: Any) -> int:
        """Parse gesture confidence value (0-100)."""
        if value is None:
            return 0
        try:
            return max(0, min(100, int(value)))
        except (ValueError, TypeError):
            return 0

    def parse_orientation(self, value: Any) -> Any:
        """Pass through orientation value without type restriction.

        Devices may use xyz for different data types (angles, acceleration,
        enum strings, etc.), so no conversion is applied.
        """
        return value

    def extract_events(
        self,
        sensor_data: dict[str, Any],
        base_events: dict[str, bool] | None = None,
    ) -> dict[str, bool]:
        """Extract gesture event states from sensor data.

        When base_events is provided, preserves existing values for event keys
        not present in sensor_data. This handles devices that send properties
        incrementally across multiple messages — an intermediate update
        (e.g. confidence only) won't overwrite a previously-set event flag.
        """
        events = dict(base_events) if base_events else {}
        for config in self.GESTURE_CONFIGS.values():
            if event_attr := config.event_attr:
                if event_attr in sensor_data:
                    events[event_attr] = self.parse_event_value(sensor_data[event_attr])
                elif event_attr not in events:
                    events[event_attr] = False
        return events

    def initialize_events(self) -> dict[str, bool]:
        """Initialize gesture event flags to False."""
        return {
            config.event_attr: False
            for config in self.GESTURE_CONFIGS.values()
            if config.event_attr is not None
        }

    def reset_events(self, events: dict[str, bool]) -> dict[str, bool]:
        """Reset all gesture event flags to False."""
        return dict.fromkeys(events, False)

    def process_update(
        self,
        sensor_data: dict[str, Any],
        previous_events: dict[str, bool],
        current_gesture: str = GESTURE_IDLE,
        current_display_duration: float = 2.0,
        current_power: bool | None = None,
        current_sensitivity: int | None = None,
        current_confidence: int | None = None,
    ) -> GestureUpdateResult:
        """Process gesture sensor update data (only contains non-None values).

        Args:
            sensor_data: Dictionary of sensor data from the device.
            previous_events: Previous event states for transition detection.
            current_gesture: Current gesture state to preserve if not in update.
            current_display_duration: Current display duration to preserve.
            current_power: Current power state (None if device doesn't report).
            current_sensitivity: Current sensitivity (None if device doesn't report).
            current_confidence: Current confidence to preserve between messages.

        Returns:
            GestureUpdateResult with updated values.
        """
        result = GestureUpdateResult()
        result.gesture = current_gesture
        result.display_duration = current_display_duration
        result.power = current_power
        result.sensitivity = current_sensitivity
        # Preserve confidence between messages (cleared by timer when idle)
        result.confidence = current_confidence
        # Update display duration
        if KEY_GESTURE_DISPLAY_DURATION in sensor_data:
            with contextlib.suppress(ValueError, TypeError):
                result.display_duration = float(
                    sensor_data[KEY_GESTURE_DISPLAY_DURATION]
                )

        # Process gesture type
        gesture_triggered_by_type = False
        if KEY_GESTURE_TYPE in sensor_data:
            gesture_type = self.normalize_gesture(sensor_data[KEY_GESTURE_TYPE])
            result.gesture = gesture_type
            if gesture_type != GESTURE_IDLE:
                gesture_triggered_by_type = True

        # Update confidence only if present in sensor_data
        if KEY_GESTURE_CONFIDENCE in sensor_data:
            result.confidence = self.parse_confidence(
                sensor_data[KEY_GESTURE_CONFIDENCE]
            )

        # Process event flags and detect transitions.
        # Pass previous_events as base so that incremental updates
        # (e.g. confidence-only) don't wipe active event flags.
        result.events = self.extract_events(sensor_data, previous_events)
        triggered_gestures: list[str] = []

        for gesture_type, config in self.GESTURE_CONFIGS.items():
            if event_attr := config.event_attr:
                if result.events.get(event_attr, False) and not previous_events.get(
                    event_attr, False
                ):
                    triggered_gestures.append(gesture_type)

        # Handle triggered gestures (use last one if multiple)
        if triggered_gestures:
            if len(triggered_gestures) > 1:
                _LOGGER.debug(
                    "Multiple gestures triggered simultaneously: %s, using %s",
                    triggered_gestures,
                    triggered_gestures[-1],
                )
            result.gesture = triggered_gestures[-1]

        result.gesture_triggered = gesture_triggered_by_type or bool(triggered_gestures)

        # Ensure event flag is set for triggered gesture.
        # Handles case where gesture is triggered by type (not event transition).
        if result.gesture_triggered:
            if event_attr := self.get_event_attr(result.gesture):
                result.events[event_attr] = True

        # Extract orientation data - only update if present in sensor_data
        if KEY_X_ORIENTATION in sensor_data:
            result.orientation[KEY_ORIENTATION_X] = self.parse_orientation(
                sensor_data[KEY_X_ORIENTATION]
            )
        if KEY_Y_ORIENTATION in sensor_data:
            result.orientation[KEY_ORIENTATION_Y] = self.parse_orientation(
                sensor_data[KEY_Y_ORIENTATION]
            )
        if KEY_Z_ORIENTATION in sensor_data:
            result.orientation[KEY_ORIENTATION_Z] = self.parse_orientation(
                sensor_data[KEY_Z_ORIENTATION]
            )
        if KEY_ORIENTATION_CHANGE in sensor_data:
            result.orientation[KEY_ORIENTATION_CHANGE_SHORT] = self.parse_orientation(
                sensor_data[KEY_ORIENTATION_CHANGE]
            )

        # Update power and sensitivity only if present in sensor_data
        if KEY_POWER in sensor_data:
            result.power = self.parse_event_value(sensor_data[KEY_POWER])
        if KEY_SENSITIVITY in sensor_data:
            with contextlib.suppress(ValueError, TypeError):
                result.sensitivity = int(sensor_data[KEY_SENSITIVITY])

        return result


def get_gesture_icon(gesture_type: str) -> str:
    """Get icon for gesture type."""
    return GESTURE_ICONS.get(gesture_type, DEFAULT_GESTURE_ICON)


def get_gesture_display_name(gesture_type: str) -> str:
    """Get display name for gesture type."""
    config = GestureProcessor.GESTURE_CONFIGS.get(gesture_type)
    if config:
        return config.display_name
    return gesture_type.replace("_", " ").title()
