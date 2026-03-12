# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Threshold management for ESP-Weaver integration.

This module provides the ThresholdManager class for threshold-related functionality:
- Threshold event handlers and listener setup
- Sensor discovery helpers for threshold entities

Note: Pure Python threshold utilities (parsing, validation) are in
iot.utils.number_utils.
"""

from collections.abc import Callable, Mapping
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import CACHE_SENSORS
from ..iot.specs.device_specs import DEFAULT_DEVICE_NAME_PREFIX
from ..iot.specs.events import (
    EVENT_DEVICE_THRESHOLD_REPORT,
    EVENT_SENSOR_DISCOVERED,
    EVENT_SENSOR_THRESHOLD_UPDATED,
    EVENT_THRESHOLD_DATA_RECEIVED,
    EVENT_THRESHOLD_UPDATE_TO_DEVICE,
)
from ..iot.specs.keys import (
    CONF_NODE_ID,
    KEY_API,
    KEY_DEVICE_INFO,
    KEY_ENTITY,
    KEY_NAME,
    KEY_PARAM_NAME,
    KEY_RAW_TYPE,
    KEY_SENSOR_NAME,
    KEY_SENSOR_TYPE,
    KEY_SOURCE,
    KEY_THRESHOLD_DATA,
    KEY_THRESHOLD_TYPE,
    KEY_TIMESTAMP,
    KEY_VALUE,
    SOURCE_DEVICE_THRESHOLD_REPORT,
    SOURCE_NUMBER_ENTITY,
)
from ..iot.specs.sensor_specs import THRESHOLD_SENSOR_TYPES
from ..iot.utils.number_utils import parse_threshold_params

if TYPE_CHECKING:
    from ..coordinator import ESPDataUpdateCoordinator
    from ..iot.client.device_api import ESPWeaverApi

_LOGGER = logging.getLogger(__name__)


class ThresholdManager:
    """Manages threshold-related operations for ESP devices.

    Handles threshold event handlers, listener setup,
    and sensor discovery helpers for threshold entities.

    For pure Python utilities (parsing, validation), use:
        from iot.utils.number_utils import ...
    """

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        api: "ESPWeaverApi | None" = None,
    ) -> None:
        """Initialize the threshold manager.

        Args:
            hass: Home Assistant instance.
            domain: Integration domain name.
            api: Optional API instance for device communication.
                 If not provided, will be looked up from hass.data on each call.
        """
        self.hass = hass
        self.domain = domain
        self._api = api

    def _get_api(self) -> "ESPWeaverApi | None":
        """Get API instance, using cached reference or hass.data lookup.

        Returns:
            API instance or None if not found.
        """
        if self._api is not None:
            return self._api
        domain_data = self.hass.data.get(self.domain, {})
        api = domain_data.get(KEY_API)
        if api is None:
            return None
        # Cast from Any to ESPWeaverApi
        result: ESPWeaverApi = api
        return result

    # Event Handlers

    def create_report_handler(self, node_id: str) -> Callable:
        """Create device threshold report event handler.

        Args:
            node_id: Device node ID.

        Returns:
            Async event handler function.
        """
        hass = self.hass

        async def handle_device_threshold_report(event: Event) -> None:
            """Handle device threshold report events."""
            event_node_id = event.data.get(CONF_NODE_ID, "")
            if event_node_id != node_id:
                return

            threshold_data = event.data.get(KEY_THRESHOLD_DATA, {})
            sensor_types = parse_threshold_params(threshold_data)

            for sensor_type in sensor_types:
                hass.bus.async_fire(
                    EVENT_SENSOR_DISCOVERED,
                    {
                        CONF_NODE_ID: node_id,
                        KEY_SENSOR_TYPE: sensor_type,
                        KEY_SENSOR_NAME: (
                            f"{sensor_type.replace('_', ' ').title()} Sensor"
                        ),
                        KEY_DEVICE_INFO: {
                            CONF_NODE_ID: node_id,
                            KEY_NAME: f"{DEFAULT_DEVICE_NAME_PREFIX}{node_id}",
                        },
                        KEY_THRESHOLD_DATA: {
                            k: v for k, v in threshold_data.items() if sensor_type in k
                        },
                        KEY_SOURCE: SOURCE_DEVICE_THRESHOLD_REPORT,
                    },
                )

                min_val = threshold_data.get(f"{sensor_type}_min_threshold")
                max_val = threshold_data.get(f"{sensor_type}_max_threshold")
                if min_val is not None or max_val is not None:
                    hass.bus.async_fire(
                        EVENT_THRESHOLD_DATA_RECEIVED,
                        {
                            CONF_NODE_ID: node_id,
                            KEY_THRESHOLD_DATA: {
                                f"{sensor_type}_min_threshold": min_val,
                                f"{sensor_type}_max_threshold": max_val,
                            },
                            KEY_TIMESTAMP: dt_util.utcnow().isoformat(),
                        },
                    )

        return handle_device_threshold_report

    def create_update_handler(self, node_id: str) -> Callable:
        """Create number threshold update event handler.

        Args:
            node_id: Device node ID.

        Returns:
            Async event handler function.
        """
        hass = self.hass
        get_api = self._get_api

        async def handle_threshold_update(event: Event) -> None:
            """Handle threshold update events from number platform."""
            event_node_id = event.data.get(CONF_NODE_ID, "")
            if event_node_id != node_id:
                return

            source = event.data.get(KEY_SOURCE, "")
            if source != SOURCE_NUMBER_ENTITY:
                return

            api_instance = get_api()
            if not api_instance:
                _LOGGER.warning("Shared API instance not found")
                return

            param_name = event.data.get(KEY_PARAM_NAME, "")
            value = event.data.get(KEY_VALUE)
            sensor_type = event.data.get(KEY_SENSOR_TYPE, "")
            threshold_type = event.data.get(KEY_THRESHOLD_TYPE, "")

            # Validate value before calling API
            if value is None:
                _LOGGER.warning(
                    "Skipping threshold update: value is None for param=%s, "
                    "sensor_type=%s, threshold_type=%s",
                    param_name,
                    sensor_type,
                    threshold_type,
                )
                return

            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _LOGGER.warning(
                    "Skipping threshold update: value '%s' is not a number for "
                    "param=%s, sensor_type=%s, threshold_type=%s",
                    value,
                    param_name,
                    sensor_type,
                    threshold_type,
                )
                return

            try:
                success = await api_instance.set_local_ctrl_property(
                    node_id, param_name, value
                )
            except (OSError, TimeoutError, ConnectionError, RuntimeError) as err:
                _LOGGER.error(
                    "Error sending threshold to device: node=%s, param=%s: %s",
                    node_id,
                    param_name,
                    err,
                )
                return

            if success:
                hass.bus.async_fire(
                    EVENT_SENSOR_THRESHOLD_UPDATED,
                    {
                        CONF_NODE_ID: node_id,
                        KEY_SENSOR_TYPE: sensor_type,
                        KEY_THRESHOLD_TYPE: threshold_type,
                        KEY_VALUE: value,
                    },
                )
            else:
                _LOGGER.error(
                    "Failed to send threshold to device: node=%s, param=%s",
                    node_id,
                    param_name,
                )

        return handle_threshold_update

    def setup_listeners(self, node_id: str) -> list[Callable[[], None]]:
        """Set up threshold-related event listeners.

        Args:
            node_id: Device node ID.

        Returns:
            List of unsubscribe functions to clean up listeners.
        """
        unsubscribe_callbacks: list[Callable[[], None]] = []

        report_handler = self.create_report_handler(node_id)
        unsubscribe_callbacks.append(
            self.hass.bus.async_listen(EVENT_DEVICE_THRESHOLD_REPORT, report_handler)
        )

        update_handler = self.create_update_handler(node_id)
        unsubscribe_callbacks.append(
            self.hass.bus.async_listen(EVENT_THRESHOLD_UPDATE_TO_DEVICE, update_handler)
        )

        return unsubscribe_callbacks

    # Sensor Discovery Helpers

    def replay_discovered_sensors(
        self,
        node_id: str,
        get_sensor_info_fn: Callable,
        coordinator: "ESPDataUpdateCoordinator",
    ) -> None:
        """Replay discovered sensors to create threshold entities.

        Args:
            node_id: Device node ID.
            get_sensor_info_fn: Function to extract sensor info from entity.
            coordinator: ESPDataUpdateCoordinator instance.
        """
        discovered_sensors = coordinator.discovered_entities.get(CACHE_SENSORS, {})

        if not discovered_sensors:
            return

        for entity in discovered_sensors.values():
            sensor_entity = (
                entity.get(KEY_ENTITY) if isinstance(entity, dict) else entity
            )
            if not sensor_entity:
                continue

            sensor_info = get_sensor_info_fn(sensor_entity)
            if not sensor_info:
                continue

            sensor_type = sensor_info.get(KEY_SENSOR_TYPE)
            entity_node_id = sensor_info.get(CONF_NODE_ID)
            sensor_name = sensor_info.get(KEY_SENSOR_NAME)

            if not all((sensor_type, entity_node_id, sensor_name)):
                continue

            if entity_node_id != node_id:
                continue

            if sensor_type not in THRESHOLD_SENSOR_TYPES:
                continue

            self.hass.bus.async_fire(
                EVENT_SENSOR_DISCOVERED,
                {
                    CONF_NODE_ID: entity_node_id,
                    KEY_SENSOR_TYPE: sensor_type,
                    KEY_SENSOR_NAME: sensor_name,
                    KEY_DEVICE_INFO: {
                        CONF_NODE_ID: entity_node_id,
                        KEY_NAME: f"{DEFAULT_DEVICE_NAME_PREFIX}{entity_node_id!s}",
                    },
                },
            )

    def extract_discovery_event_data(
        self,
        event_data: Mapping[str, Any],
    ) -> tuple[str | None, str | None, str | None]:
        """Extract and normalize discovery event data for threshold processing.

        Args:
            event_data: Event data dictionary.

        Returns:
            Tuple of (node_id, sensor_type, device_name) or (None, None, None).
        """
        device_info = event_data.get(KEY_DEVICE_INFO, {})

        # Prefer device_info, use event_data if not present
        event_node_id = device_info.get(CONF_NODE_ID)
        if not event_node_id:
            event_node_id = event_data.get(CONF_NODE_ID)

        if not event_node_id:
            return None, None, None

        event_node_id = str(event_node_id).strip()

        raw_type = event_data.get(KEY_RAW_TYPE, "")
        sensor_type = raw_type if raw_type else event_data.get(KEY_SENSOR_TYPE, "")

        if not sensor_type:
            return event_node_id, None, None

        # Inline check: is this a threshold parameter name?
        if "threshold" in sensor_type.lower():
            return event_node_id, None, None

        if sensor_type not in THRESHOLD_SENSOR_TYPES:
            return event_node_id, None, None

        device_name = device_info.get(
            KEY_NAME, f"{DEFAULT_DEVICE_NAME_PREFIX}{event_node_id}"
        )
        return event_node_id, sensor_type, device_name
