# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Entity state definitions for ESP IoT.

This module provides data classes that are independent of Home Assistant,
allowing them to be used in both the core layer and HA layer.

Note: DeviceInfo in managers/device_registry.py is a different class used for
runtime device management with mutable state. The classes here are for
entity state representation.
"""

from dataclasses import dataclass, field
from typing import Any

# Entity State Types


@dataclass(slots=True)
class LightState:
    """Light entity state.

    All optional attributes default to None, indicating the device doesn't
    support that feature. Values are set when device reports the parameter.

    Attributes:
        is_on: Whether the light is on.
        brightness: Brightness level 0-255 (None if device doesn't support).
        hs_color: Hue and saturation tuple (None if device doesn't support).
        intensity: Light intensity (None if device doesn't support).
        light_mode: Light mode 0-5 (None if device doesn't support).
    """

    is_on: bool = False
    # All features default to None - set when device reports the parameter
    brightness: int | None = None
    hs_color: tuple[float, float] | None = None
    intensity: int | None = None
    light_mode: int | None = None


@dataclass(slots=True)
class BatteryState:
    """Battery entity state.

    All optional attributes default to None, indicating the device doesn't
    report that parameter.

    Attributes:
        level: Battery level percentage (0-100).
        voltage: Battery voltage in volts (None if not reported).
        temperature: Battery temperature in Celsius (None if not reported).
        charging_status: Charging status string (None if not reported).
        alert_level: Alert level string (None if not reported).
    """

    level: int = 0
    # Optional features: None means device doesn't report this parameter
    voltage: float | None = None
    temperature: float | None = None
    charging_status: str | None = None
    alert_level: str | None = None


@dataclass(slots=True)
class GestureState:
    """IMU gesture entity state.

    All optional attributes default to None, indicating the device doesn't
    report that parameter.

    Attributes:
        gesture: Current gesture type.
        confidence: Gesture confidence level 0-100 (None if not reported).
        display_duration: How long to display gesture before reset.
        power: Whether IMU is powered on (None if not reported).
        sensitivity: IMU sensitivity level (None if not reported).
        orientation_x: X-axis data, type varies by firmware.
        orientation_y: Y-axis data, type varies by firmware.
        orientation_z: Z-axis data, type varies by firmware.
        orientation_change: Orientation change data (None if not reported).
    """

    gesture: str = "idle"
    display_duration: float = 2.0
    # Optional features: None means device doesn't report this parameter
    confidence: int | None = None
    power: bool | None = None
    sensitivity: int | None = None
    orientation_x: Any = None
    orientation_y: Any = None
    orientation_z: Any = None
    orientation_change: Any = None


@dataclass(slots=True)
class InputState:
    """Interactive input entity state.

    Attributes:
        input_type: Type of input (button, dial, etc.).
        last_event: Last input event detected.
        input_value: Current input value.
        sensitivity: Input sensitivity level.
        input_config: Input configuration data.
        input_mapping: Input mapping data.
        last_update: Timestamp of last update.
    """

    input_type: str = "button"
    last_event: str = "none"
    # input_value type depends on input_type (button→bool, dial→int, slider→float, etc.)
    input_value: bool | int | float | str | None = None
    sensitivity: int | None = None
    input_config: dict[str, Any] | None = None
    input_mapping: dict[str, Any] | None = None
    last_update: float = 0.0


@dataclass(slots=True)
class SleepState:
    """Low power sleep entity state.

    Attributes:
        sleep_state: Current sleep state (awake, light_sleep, deep_sleep).
        wake_reason: Reason for last wake up.
        wake_window_status: Wake window status (None if not reported).
        sleep_duration: Duration of last sleep in seconds (None if not reported).
        wake_count: Total wake count (None if device doesn't report).
        last_wake_time: Timestamp of last wake (None if not reported).
    """

    sleep_state: str = "awake"
    wake_reason: str = "unknown"
    wake_window_status: str | None = None
    sleep_duration: int | None = None
    # Optional feature: None means device doesn't report this parameter
    wake_count: int | None = None
    last_wake_time: float | None = None


# Discovery Types


@dataclass(slots=True)
class DiscoveredDevice:
    """Discovered device information.

    Attributes:
        ip: Device IP address.
        node_id: Unique device identifier.
        port: Device port (default 8080).
        device_name: User-friendly device name.
        security_version: Security version (0 = no auth, 1 = requires PoP).
        security_info: Security configuration (e.g., PoP).
    """

    ip: str
    node_id: str
    port: int = 8080
    device_name: str | None = None
    security_version: int | None = None
    security_info: dict[str, Any] = field(default_factory=dict)

    def get_simple_name(self, prefix: str = "ESP-") -> str:
        """Get simple device name.

        Args:
            prefix: Prefix to use if device_name is not set.

        Returns:
            Device name if set, otherwise prefix + node_id.
        """
        if self.device_name:
            return self.device_name
        return f"{prefix}{self.node_id}"

    @property
    def display_name(self) -> str:
        """Generate display name from device attributes."""
        name = self.get_simple_name()
        sec_info = (
            f" [Sec{self.security_version}]"
            if self.security_version is not None
            else ""
        )
        return f"{name} ({self.ip}){sec_info}"


__all__ = [
    "BatteryState",
    "DiscoveredDevice",
    "GestureState",
    "InputState",
    "LightState",
    "SleepState",
]
