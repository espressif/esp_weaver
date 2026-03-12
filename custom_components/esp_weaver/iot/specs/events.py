# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Event name constants for ESP-Weaver integration."""

from typing import Final

# DOMAIN

DOMAIN: Final = "esp_weaver"

# CONNECTION EVENTS

EVENT_CONNECTION_ERROR: Final = f"{DOMAIN}_connection_error"

# PLATFORM DISCOVERY EVENTS

EVENT_PLATFORM_DISCOVERED: Final = f"{DOMAIN}_platform_discovered"

# LIGHT EVENTS

EVENT_LIGHT_DISCOVERED: Final = f"{DOMAIN}_light_discovered"
EVENT_LIGHT_UPDATE: Final = f"{DOMAIN}_light_update"
EVENT_LIGHT_SET_PROPERTIES: Final = f"{DOMAIN}_light_set_properties"

# SENSOR EVENTS

EVENT_SENSOR_DISCOVERED: Final = f"{DOMAIN}_sensor_discovered"
EVENT_SENSOR_UPDATE: Final = f"{DOMAIN}_sensor_update"

# BINARY SENSOR EVENTS

EVENT_BINARY_SENSOR_DISCOVERED: Final = f"{DOMAIN}_binary_sensor_discovered"
EVENT_BINARY_SENSOR_UPDATE: Final = f"{DOMAIN}_binary_sensor_update"

# THRESHOLD / NUMBER EVENTS

EVENT_THRESHOLD_UPDATE_TO_DEVICE: Final = f"{DOMAIN}_threshold_update_to_device"
EVENT_THRESHOLD_DATA_RECEIVED: Final = f"{DOMAIN}_threshold_data_received"
EVENT_SENSOR_THRESHOLD_UPDATED: Final = f"{DOMAIN}_sensor_threshold_updated"
EVENT_DEVICE_THRESHOLD_REPORT: Final = f"{DOMAIN}_device_threshold_report"

# BATTERY ENERGY EVENTS

EVENT_BATTERY_ENERGY_DISCOVERED: Final = f"{DOMAIN}_battery_energy_discovered"
EVENT_BATTERY_ENERGY_UPDATE: Final = f"{DOMAIN}_battery_energy_update"

# IMU GESTURE EVENTS

EVENT_IMU_GESTURE_DISCOVERED: Final = f"{DOMAIN}_imu_gesture_discovered"
EVENT_IMU_GESTURE_UPDATE: Final = f"{DOMAIN}_imu_gesture_update"

# INTERACTIVE INPUT EVENTS

EVENT_INTERACTIVE_INPUT_DISCOVERED: Final = f"{DOMAIN}_interactive_input_discovered"
EVENT_INTERACTIVE_INPUT_UPDATE: Final = f"{DOMAIN}_interactive_input_update"

# LOW POWER SLEEP EVENTS

EVENT_LOW_POWER_SLEEP_DISCOVERED: Final = f"{DOMAIN}_low_power_sleep_discovered"
EVENT_LOW_POWER_SLEEP_UPDATE: Final = f"{DOMAIN}_low_power_sleep_update"
