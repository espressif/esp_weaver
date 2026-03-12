# ESP-Weaver Integration

English | [简体中文](README_zh.md)

ESP-Weaver is a local integration for Home Assistant that enables you to control and manage IoT devices based on Espressif (ESP) chips (ESP32, ESP32-S3, ESP32-C series) within Home Assistant.

> [!NOTE]
> The name "Weaver" is inspired by the weaver bird — a master nest-builder that transforms scattered blades of grass into elaborate, tightly-woven structures. Similarly, ESP-Weaver brings together isolated ESP devices on your network and integrates them into a cohesive smart home within Home Assistant.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Device Firmware](#device-firmware)
- [Device Configuration](#device-configuration)
- [Architecture](#architecture)
- [References](#references)

## Features

- **Local Communication**: All data exchange occurs within the LAN
- **Auto Discovery**: Automatic device detection via mDNS/Zeroconf protocol
- **Real-time Sync**: HTTP long-polling mechanism for real-time state updates
- **Secure Transport**: Support for ESP Local Control Sec0/Sec1 encryption protocols
- **Entity Support**: Support for Light, Sensor, Binary Sensor, Number, and other entity types
- **Auto Reconnection**: Device online status detection with automatic connection recovery

### Supported Entity Types

The integration automatically creates corresponding Home Assistant entities based on device-reported configuration:

| Entity Type | Description |
|-------------|-------------|
| **Light** | Power control, brightness, hue/saturation, color temperature, effects |
| **Sensor** | Temperature, humidity, pressure, and other environmental data |
| **Binary Sensor** | Door/window state, motion detection, vibration, touch sensing |
| **Number** | Adjustable threshold parameters |
| **Battery Energy** | Battery level, voltage, temperature, charging status |
| **IMU Gesture** | IMU gesture recognition (toss, flip, shake, rotation, etc.) |
| **Interactive Input** | Input events and values |
| **Low Power Sleep** | Sleep state, wake reason, wake window |

## Installation

> **Requirements**:
> - Home Assistant Core ≥ 2025.12.5
> - Python ≥ 3.13.2

### Option 1: HACS Installation (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed
2. Click **HACS** in the sidebar to open Home Assistant Community Store
3. Click menu (⋮) → **Custom repositories**
4. In the dialog:
   - **Repository**: Enter `https://github.com/espressif/esp_weaver`
   - **Type**: Select **Integration**
5. Click **ADD**
6. Return to HACS main page, search for **ESP-Weaver** and click **DOWNLOAD**
7. Restart Home Assistant

### Option 2: Script Installation

For users with SSH/terminal access to Home Assistant.

```bash
# Clone the repository
git clone https://github.com/espressif/esp_weaver.git
cd esp_weaver

# Run the installer (replace /config with your HA config path)
./install.sh /config
```

Common config paths:
| Installation Type | Config Path |
|-------------------|-------------|
| Home Assistant OS | `/config` |
| Home Assistant Core | `~/.homeassistant` |
| Home Assistant Supervised | `/usr/share/hassio/homeassistant` |

After installation, restart Home Assistant.

### Option 3: Samba Share (Home Assistant OS/Supervised)

For Home Assistant OS or Supervised installations.

#### Step 1: Install Samba Share Add-on

1. Navigate to **Settings** → **Add-ons** → **Add-on Store**
2. Search for `Samba share` and install
3. Configure username and password in add-on settings
4. Start the add-on

#### Step 2: Connect to Samba Share

Enter the following address in your file manager:

| OS | Connection Address |
|:---|:-------------------|
| Windows | `\\homeassistant.local` or `\\<IP_ADDRESS>` |
| macOS | `smb://homeassistant.local/config` or `smb://<IP_ADDRESS>/config` |
| Linux | `smb://homeassistant.local/config` or `smb://<IP_ADDRESS>/config` |

Enter the **username** and **password** configured in the Samba Share add-on when prompted.

> [!TIP]
> - If `homeassistant.local` doesn't work, use your Home Assistant's IP address instead
> - You can find the IP address in **Settings** → **System** → **Network** or your router's admin page
> - **macOS users**: Press `⌘+K` in Finder to open "Connect to Server"
> - **Linux users**: Press `Ctrl+L` in file manager to show address bar, or click "Other Locations" in sidebar

#### Step 3: Copy Integration Files

1. Clone the repository: `git clone https://github.com/espressif/esp_weaver.git`
2. Copy the `esp_weaver/custom_components` folder from the repository into the `config` folder in the Samba share

Final directory structure:

```
config/
└── custom_components/
    └── esp_weaver/
        ├── __init__.py
        ├── manifest.json
        └── ...
```

#### Step 4: Restart Home Assistant

## Device Firmware

ESP-Weaver requires devices running compatible firmware that supports ESP Weaver local control protocol. The device-side SDK component (`esp_weaver`) and example firmware are available in the [esp-iot-solution](https://github.com/espressif/esp-iot-solution) repository.

### Supported Targets

| ESP32 | ESP32-C2 | ESP32-C3 | ESP32-C5 | ESP32-C6 | ESP32-C61 | ESP32-S3 |
| ----- | -------- | -------- | -------- | -------- | --------- | -------- |
|   ✓   |    ✓     |    ✓     |    ✓     |    ✓     |     ✓     |    ✓     |

### Getting the Firmware

```bash
# Clone the repository
git clone https://github.com/espressif/esp-iot-solution.git
cd esp-iot-solution/examples/weaver
```

### Available Examples

The repository provides ready-to-use example projects:

| Example | Description |
|---------|-------------|
| `led_light` | Smart light device with power switch, brightness, HSV color control, and color temperature |
| `imu_gesture` | IMU gesture sensor with multiple gesture recognition (flip, shake, rotation, clap, etc.) |

### Building and Flashing

Please refer to the [ESP Weaver Component README](https://github.com/espressif/esp-iot-solution/tree/master/components/esp_weaver) and each example's README for detailed build and flash instructions.

> [!TIP]
> For detailed build instructions and device-specific configuration, refer to the README in each example directory.

## Device Configuration

The integration supports auto-discovery of ESP devices on the local network:

1. Navigate to **Settings** → **Devices & Services** → **Add Integration**
2. Search and select **ESP-Weaver**
3. Choose target device from discovered device list
4. If device has security mode enabled (Sec1), enter PoP credentials

Upon completion, device entities will be automatically registered in Home Assistant.

## Architecture

This integration follows a layered architecture adhering to the single responsibility principle:

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant Core                       │
├─────────────────────────────────────────────────────────────┤
│  Platforms: light.py, sensor.py, binary_sensor.py, ...      │
├─────────────────────────────────────────────────────────────┤
│  coordinator.py (ESPDataUpdateCoordinator)                  │
│  └── Data updates, availability checks, reconnection        │
├─────────────────────────────────────────────────────────────┤
│  iot/client/device_api.py (ESPWeaverApi)                        │
│  └── Main API coordinator, delegates to managers            │
├─────────────────────────────────────────────────────────────┤
│  iot/managers/ (Manager Classes)                            │
│  ├── DeviceRegistry      - Device state registry            │
│  ├── AvailabilityManager - Availability detection           │
│  ├── ConnectionManager   - Connection lifecycle             │
│  ├── PropertyManager     - Property read/write & messaging  │
│  └── DeviceDiscoveryManager - Entity discovery              │
├─────────────────────────────────────────────────────────────┤
│  iot/client/client.py (ESPLocalCtrlClient)                  │
│  └── ESP Local Control protocol implementation              │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **Coordinator** | `coordinator.py` | Data polling, availability checks, connection monitoring |
| **Entity Base** | `entity.py` | Base entity class with availability mixin |
| **ESPWeaverApi** | `iot/client/device_api.py` | Top-level API coordinator |
| **DeviceRegistry** | `iot/managers/device_registry.py` | Centralized device state management |
| **ConnectionManager** | `iot/managers/connection_manager.py` | Connection establishment, reconnection |
| **PropertyManager** | `iot/managers/property_manager.py` | Property operations, event dispatching |
| **DeviceDiscoveryManager** | `iot/managers/entity_discovery.py` | Configuration parsing, entity discovery |
| **ESPLocalCtrlClient** | `iot/client/client.py` | Protocol-level device communication |

## References

| Resource | Link |
|----------|------|
| ESP Weaver SDK | [esp-iot-solution/components/esp_weaver](https://github.com/espressif/esp-iot-solution/tree/master/components/esp_weaver) |
| ESP Local Control | [ESP-IDF Programming Guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_local_ctrl.html) |
| Home Assistant Dev | [developers.home-assistant.io](https://developers.home-assistant.io/) |
