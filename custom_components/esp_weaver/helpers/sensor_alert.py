# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Sensor threshold alert service for ESP-Weaver integration.

This module provides the SensorAlertService class for handling threshold
violations and persistent notifications for sensor entities.
"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    ALERT_TYPE_HIGH,
    ALERT_TYPE_LOW,
    SERVICE_CREATE,
    SERVICE_DISMISS,
    SERVICE_PERSISTENT_NOTIFICATION,
)
from ..iot.specs.keys import KEY_NOTIFICATION_ID
from ..iot.specs.sensor_specs import get_base_sensor_type, get_sensor_unit
from ..iot.utils.number_utils import (
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    hpa_to_inhg,
    inhg_to_hpa,
    is_imperial_unit_system,
)
from ..iot.utils.sensor_utils import (
    build_threshold_notification_data,
    check_threshold_violation,
    get_normalized_sensor_type,
    was_previously_violated,
)

_LOGGER = logging.getLogger(__name__)


class SensorAlertService:
    """Service for sensor threshold alerts and notifications.

    Handles:
    - Threshold violation checking
    - Creating/clearing persistent notifications in Home Assistant

    Attributes:
        hass: Home Assistant instance
        domain: Integration domain name
    """

    def __init__(self, hass: HomeAssistant, domain: str) -> None:
        """Initialize the sensor alert service.

        Args:
            hass: Home Assistant instance
            domain: Integration domain name (e.g., "esp_weaver")
        """
        self.hass = hass
        self.domain = domain
        # Track active violations to avoid repeated alerts
        # Key: {node_id}_{sensor_type}, Value: violation_type
        self._active_violations: dict[str, str] = {}

    async def check_and_handle_violations(
        self,
        node_id: str,
        device_name: str,
        sensor_type: str,
        current_value: float,
        old_value: float | None,
    ) -> None:
        """Check threshold violations and handle alerts.

        Args:
            node_id: Device node ID
            device_name: Device display name
            sensor_type: Type of sensor (e.g., "temperature")
            current_value: Current sensor value
            old_value: Previous sensor value (for violation clear check)
        """
        try:
            raw_sensor_type = sensor_type.lower()
            normalized_type = get_normalized_sensor_type(raw_sensor_type)
            base_type = get_base_sensor_type(normalized_type)

            # Use raw_sensor_type for entity lookup (matches threshold entities)
            # Use normalized_type/base_type for unit conversion logic
            thresholds = self._get_threshold_values(node_id, raw_sensor_type)
            if thresholds is None:
                return

            min_threshold, max_threshold = thresholds
            unit = get_sensor_unit(normalized_type) or ""

            violation_type, threshold_value = check_threshold_violation(
                current_value, min_threshold, max_threshold
            )

            # Prepare display values (convert to imperial units if needed)
            display_value = current_value
            display_threshold = threshold_value
            display_unit = unit

            if is_imperial_unit_system(self.hass):
                if base_type == "temperature":
                    display_value = celsius_to_fahrenheit(current_value)
                    if threshold_value is not None:
                        display_threshold = celsius_to_fahrenheit(threshold_value)
                    display_unit = "°F"
                elif base_type == "pressure":
                    display_value = hpa_to_inhg(current_value)
                    if threshold_value is not None:
                        display_threshold = hpa_to_inhg(threshold_value)
                    display_unit = "inHg"
                # Note: illuminance stays in lx - HA does not auto-convert to fc

            violation_key = f"{node_id}_{normalized_type}"

            if violation_type and threshold_value is not None:
                # Ensure display_threshold has correct type for mypy
                # (it's guaranteed non-None when threshold_value is not None)
                actual_display_threshold = (
                    display_threshold
                    if display_threshold is not None
                    else threshold_value
                )
                # Check if this is a new violation or same ongoing violation
                previous_violation = self._active_violations.get(violation_key)
                if previous_violation != violation_type:
                    # Dismiss previous notification if violation type changed
                    if previous_violation is not None:
                        await self._dismiss_single_notification(
                            node_id, normalized_type, previous_violation
                        )
                    self._active_violations[violation_key] = violation_type
                    await self._create_notification(
                        node_id,
                        device_name,
                        normalized_type,
                        violation_type,
                        display_value,
                        actual_display_threshold,
                        display_unit,
                    )
                # If same violation type continues, skip sending repeated alerts
            elif (
                was_previously_violated(old_value, min_threshold, max_threshold)
                or violation_key in self._active_violations
            ):
                # Was violated, now back to normal - clear violation state
                self._active_violations.pop(violation_key, None)
                await self._clear_notifications(node_id, normalized_type)

        except (ValueError, TypeError, KeyError) as err:
            # ValueError: invalid threshold values
            # TypeError: wrong types in comparison
            # KeyError: missing state data
            _LOGGER.warning(
                "Failed to check threshold violations for %s/%s: %s",
                node_id,
                sensor_type,
                err,
            )

    def _get_threshold_values(
        self, node_id: str, sensor_type: str
    ) -> tuple[float, float] | None:
        """Get min/max threshold values from number entities in native units.

        Args:
            node_id: Device node ID
            sensor_type: Sensor type for entity ID construction

        Returns:
            Tuple of (min_threshold, max_threshold) in native units, or None
        """
        # Use entity registry to find entities by unique_id
        # (entity_id format varies based on device name when has_entity_name=True)
        min_unique_id = f"{self.domain}_{node_id}_{sensor_type}_min_threshold"
        max_unique_id = f"{self.domain}_{node_id}_{sensor_type}_max_threshold"

        entity_registry = er.async_get(self.hass)
        min_entry = entity_registry.async_get_entity_id(
            "number", self.domain, min_unique_id
        )
        max_entry = entity_registry.async_get_entity_id(
            "number", self.domain, max_unique_id
        )

        if not min_entry or not max_entry:
            return None

        min_state = self.hass.states.get(min_entry)
        max_state = self.hass.states.get(max_entry)

        if not min_state or not max_state:
            return None

        try:
            min_val = float(min_state.state)
            max_val = float(max_state.state)

            # Convert display units to native units for imperial users
            base_type = get_base_sensor_type(sensor_type)
            if is_imperial_unit_system(self.hass):
                min_val, max_val = self._convert_imperial_thresholds(
                    base_type, min_val, max_val
                )

            if min_val >= max_val:
                _LOGGER.warning(
                    "Invalid thresholds for %s/%s: min (%s) >= max (%s)",
                    node_id,
                    sensor_type,
                    min_val,
                    max_val,
                )
                return None
        except (ValueError, TypeError):
            return None

        return (min_val, max_val)

    def _convert_imperial_thresholds(
        self, base_type: str, min_val: float, max_val: float
    ) -> tuple[float, float]:
        """Convert imperial display units to native units.

        Args:
            base_type: Base sensor type (temperature, pressure, illuminance)
            min_val: Minimum threshold in display units
            max_val: Maximum threshold in display units

        Returns:
            Tuple of (min_val, max_val) in native units
        """
        if base_type == "temperature":
            min_val = fahrenheit_to_celsius(min_val)
            max_val = fahrenheit_to_celsius(max_val)
        elif base_type == "pressure":
            min_val = inhg_to_hpa(min_val)
            max_val = inhg_to_hpa(max_val)
        # Note: illuminance stays in lx - HA does not auto-convert to fc,
        # so no conversion needed here
        return (min_val, max_val)

    async def _create_notification(
        self,
        node_id: str,
        device_name: str,
        sensor_type: str,
        alert_type: str,
        current_value: float,
        threshold_value: float,
        unit: str,
    ) -> None:
        """Create threshold violation notification.

        Args:
            node_id: Device node ID
            device_name: Device display name
            sensor_type: Type of sensor
            alert_type: Alert type (high/low)
            current_value: Current sensor value
            threshold_value: Threshold that was violated
            unit: Unit of measurement
        """
        try:
            notification_data = build_threshold_notification_data(
                device_name,
                node_id,
                sensor_type,
                alert_type,
                current_value,
                threshold_value,
                unit,
                self.domain,
            )
            await self.hass.services.async_call(
                SERVICE_PERSISTENT_NOTIFICATION,
                SERVICE_CREATE,
                notification_data,
            )
        except (OSError, ValueError) as err:
            # OSError: service call failed (HA errors inherit from this)
            # ValueError: invalid notification data
            _LOGGER.warning("Failed to create threshold notification: %s", err)

    async def _clear_notifications(self, node_id: str, sensor_type: str) -> None:
        """Clear threshold notifications for a sensor.

        Args:
            node_id: Device node ID
            sensor_type: Type of sensor
        """
        for alert_type in [ALERT_TYPE_HIGH, ALERT_TYPE_LOW]:
            await self._dismiss_single_notification(node_id, sensor_type, alert_type)

    async def _dismiss_single_notification(
        self, node_id: str, sensor_type: str, alert_type: str
    ) -> None:
        """Dismiss a single threshold notification.

        Args:
            node_id: Device node ID
            sensor_type: Type of sensor
            alert_type: Alert type (high/low) to dismiss
        """
        try:
            notification_id = (
                f"{self.domain}_threshold_{node_id}_{sensor_type}_{alert_type}"
            )
            await self.hass.services.async_call(
                SERVICE_PERSISTENT_NOTIFICATION,
                SERVICE_DISMISS,
                {KEY_NOTIFICATION_ID: notification_id},
            )
        except (OSError, ValueError) as err:
            # OSError: service call failed
            # ValueError: invalid notification ID
            _LOGGER.warning(
                "Failed to clear %s threshold notification: %s", alert_type, err
            )
