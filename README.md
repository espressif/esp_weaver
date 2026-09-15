# ESP-Weaver Integration

English | [简体中文](README_zh.md)

ESP-Weaver is a Home Assistant integration for controlling and managing IoT devices based on Espressif ESP32, ESP32-S3 and ESP32-C series chips from within Home Assistant. Devices are reached over the local network, through the ESP local control protocol.

> [!NOTE]
> The name "Weaver" is inspired by the weaver bird, a master nest-builder that transforms scattered blades of grass into elaborate, tightly woven structures. ESP-Weaver follows the same principle: it brings isolated ESP devices on a network together into a cohesive smart home within Home Assistant.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Device Firmware](#device-firmware)
- [Device Configuration](#device-configuration)
- [Device Profiles](#device-profiles)
- [Known Limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)
- [References](#references)

## Features

### Local Control

- **Local Communication**: All data exchange occurs within the LAN
- **Auto Discovery**: Automatic device detection via mDNS/Zeroconf protocol
- **State Polling**: Device state is read over HTTP every 60 seconds, and at once when a write goes wrong; the firmware does not push updates to Home Assistant
- **Security Modes**: Supports ESP Local Control Sec0 (unencrypted), Sec1 (PoP + AES-CTR), and Sec2 (SRP6a + AES-GCM)
- **Auto Reconnection**: Device online status detection with automatic connection recovery

### Common

- **Entity Support**: 25 Home Assistant platforms, from lights and sensors to vacuums and alarm panels

> [!NOTE]
> This release is **local control only**. RainMaker cloud control code remains in
> the repository but is not offered in the UI (`CLOUD_CONTROL_ENABLED` is off).

### Supported Entity Types

The integration automatically creates corresponding Home Assistant entities based on device-reported configuration:

| Platform | Typical devices and parameters |
|----------|-------------------------------|
| **light** | Power, brightness, hue/saturation, colour temperature, effects |
| **switch** | Switches, outlets, plugs, sockets |
| **sensor** | Temperature, humidity, pressure, battery, IMU gestures, input events |
| **binary_sensor** | Door/window state, motion, vibration, touch |
| **number** | Adjustable thresholds and intervals |
| **select** | A parameter offering a fixed set of values |
| **text** | A free-text parameter, such as a timezone |
| **button** | A parameter that triggers an action, such as reboot |
| **lock** | Locks, and the lock on a garage door |
| **fan** | Speed, direction, oscillation |
| **cover** | Blinds and garage doors, by position |
| **climate** | Air conditioners and thermostats |
| **media_player** | Speakers and TVs |
| **humidifier** | Setpoint humidity, mode, measured humidity |
| **water_heater** | Setpoint temperature, operation mode |
| **valve** | Open, close, or a position |
| **siren** | Sounding or silent |
| **vacuum** | Activity, suction, battery |
| **lawn_mower** | Mowing, paused, docked |
| **remote** | Power and the activity it is running |
| **alarm_control_panel** | Arming state and the inputs a panel detects |
| **event** | One-off happenings, such as button presses |
| **date**, **time**, **datetime** | Setpoints, determined by the parameter structure |

The entities a device generates are determined by the product profile it is
bound to when added. Espressif ships profiles for the RainMaker standard device
model and the ESP-Weaver extension types; any other product is covered by a
profile in `<config>/esp_weaver/` or in the profile repository.

## Installation

> **Requirements**:
> - Home Assistant ≥ 2025.12.5 (the core version; any installation method - OS, Supervised, Container or Core)
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

### Option 2: Samba Share (Home Assistant OS/Supervised)

For a Raspberry Pi or any other Home Assistant OS or Supervised installation.
These have no ordinary terminal, so Samba is the most direct way to reach the
files.

#### Step 1: Install Samba Share Add-on

1. Navigate to **Settings** → **Add-ons** → **Add-on Store**
2. Search for `Samba share` and install
3. Configure username and password in add-on settings; neither may be left empty
4. Start the add-on

#### Step 2: Connect to Samba Share

Enter the following address in the file manager:

| OS | Connection Address |
|:---|:-------------------|
| Windows | `\\homeassistant.local` or `\\<IP_ADDRESS>` |
| macOS | `smb://homeassistant.local/config` or `smb://<IP_ADDRESS>/config` |
| Linux | `smb://homeassistant.local/config` or `smb://<IP_ADDRESS>/config` |

Enter the **username** and **password** configured in the Samba Share add-on when prompted.

> [!TIP]
> - If `homeassistant.local` does not resolve, use the Home Assistant IP address instead
> - The IP address is shown under **Settings** → **System** → **Network**, or on the router's admin page
> - **macOS**: Press `⌘+K` in Finder to open "Connect to Server"
> - **Linux**: Press `Ctrl+L` in the file manager to show the address bar, or click "Other Locations" in the sidebar

#### Step 3: Copy Integration Files

Both steps are done on **your own computer**, and the files are then copied into
Home Assistant over the Samba share:

1. Clone the repository: `git clone https://github.com/espressif/esp_weaver.git`
2. Copy the repository's `custom_components/esp_weaver` directory into
   `custom_components/` under `config` in the Samba share, creating that directory
   if it does not exist

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

### Option 3: Script Installation (Core/Container, or with the SSH add-on)

For environments with SSH or terminal access to the configuration directory.
Home Assistant OS provides no ordinary terminal by default; use option 1 or 2
there.

```bash
# Clone the repository
git clone https://github.com/espressif/esp_weaver.git
cd esp_weaver

# Run the installer, replacing /config with the actual Home Assistant config path
./install.sh /config
```

Common config paths:
| Installation Type | Config Path |
|-------------------|-------------|
| Home Assistant Core | `~/.homeassistant` |
| Home Assistant Container | The host directory mapped to `/config` in the container |
| Home Assistant Supervised | `/usr/share/hassio/homeassistant` |
| Home Assistant OS (needs the SSH add-on) | `/config` |

After installation, restart Home Assistant.

### Removing the Integration

1. Navigate to **Settings** → **Devices & Services** → **ESP-Weaver**
2. For each device, open its menu (⋮) and choose **Delete**
3. Delete the `custom_components/esp_weaver` directory from your config directory (or remove it from HACS)
4. Restart Home Assistant

Profiles you placed in `<config>/esp_weaver/` are not touched; delete that directory too if you no longer need them.

## Device Firmware

### Local Control Firmware

Local control mode requires devices to run ESP RainMaker firmware with local control enabled (`CONFIG_ESP_RMAKER_LOCAL_CTRL_AUTO_ENABLE`, which is off in RainMaker by default; firmware without it does not advertise local control at all). The [esp-rainmaker](https://github.com/espressif/esp-rainmaker) examples - `led_light` first of all - already enable it in their `sdkconfig.defaults`, and are written with the standard RainMaker APIs (the `esp_rmaker_*` calls used in [Developing a product profile](docs/developing_a_product_profile.md)).

#### Supported Targets (Local Control)

| ESP32 | ESP32-C2 | ESP32-C3 | ESP32-C5 | ESP32-C6 | ESP32-C61 | ESP32-S3 |
| ----- | -------- | -------- | -------- | -------- | --------- | -------- |
|   ✓   |    ✓     |    ✓     |    ✓     |    ✓     |     ✓     |    ✓     |

#### Getting the Firmware

```bash
# Clone the repository
git clone https://github.com/espressif/esp-rainmaker.git
cd esp-rainmaker/examples/led_light
```

#### Available Examples

The repository provides the following ready-to-use example projects:

| Example | Description |
|---------|-------------|
| `led_light` | Smart light with power switch, brightness, HSV color control and color temperature |
| `switch` | Switch device |
| `fan` | Fan with speed control |
| `temperature_sensor` | Temperature sensor |
| `multi_device` | Several devices on one node |

Other RainMaker examples and firmware work the same way, as long as local control is enabled; the standard device types are covered by the built-in profiles.

#### Building and Flashing

These are ordinary ESP RainMaker examples: build and flash them with ESP-IDF as described in the [esp-rainmaker](https://github.com/espressif/esp-rainmaker) repository. Their `sdkconfig.defaults` already enable local control.

## Device Configuration

### Adding a Device

1. Navigate to **Settings** → **Devices & Services** → **Add Integration**
2. Search and select **ESP-Weaver**
3. Upload a profile of your own, or skip that step, then choose the device's vendor and product. See [docs/device_profiles_in_detail.md](docs/device_profiles_in_detail.md#publishing-to-the-repository) for publishing a profile to the repository

### Local Control Setup

1. The integration automatically discovers ESP devices on the local network via mDNS
2. Select the target device from the list. Devices whose advertised model is
   the product's chosen in the previous step are listed as
   `<product>-<model>-<n>`; firmware that advertises no model (ESP RainMaker
   before the `model` TXT record) is listed after them as `Other device-<n>`.
   What the device is made of is settled entity by entity once connected
3. If the device has security mode enabled (Sec1 or Sec2), enter Proof of Possession (PoP) credentials

### What the final steps report

Before the device is reached, the flow requires the device's **product**
to be selected, in two steps: vendor, then that vendor's products. Every file found is listed and
nothing is preselected; identical copies of a product are one line, while
differing versions are one line each, labelled by where they came from
(`built-in`, `repository`, `your folder`, `cached`). How the list is
assembled and labelled is covered in the guide under
[Choosing when adding a device](docs/device_profiles_in_detail.md#choosing-when-adding-a-device).

Once the device is connected, it is bound to the chosen product. What the
product resolves against the device - how many entities it will get, which it
will not, and which device types the product does not declare - is written to
the log. A device that stops answering between the handshake and this step is
not added: the flow ends with the reason, and the device is added again once
it answers.

## Device Profiles

A device reports which parameters it has; a profile defines what those parameters
correspond to in Home Assistant. This mapping layer is implemented as data rather
than code, so supporting a new device requires only YAML and no changes to the
integration.

### One product per device

A profile is a **product**: one YAML file declaring every device type that
product reports, under a `vendor` and a `product` name. Each device is bound to
exactly one product - vendor, then product, chosen before the device is
connected and confirmed against what it builds once it is - and resolves against
that file plus a built-in layer of the ten shared RainMaker services. Files are
never merged, nothing overrides anything, and nothing falls back to another
file: what the user picked is what applies.

The list is assembled from four places - your folder
(`<config>/esp_weaver/`), the built-in files, cached copies kept from earlier
bindings, and the profile repository
([`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles)).
Which product a device
belongs to is settled by its `model`; the device types it reports are then
resolved entity by entity, so a type the product declares but the device does
not report simply creates nothing. How the list is assembled, labelled and
matched is covered in the guide under
[Choosing when adding a device](docs/device_profiles_in_detail.md#choosing-when-adding-a-device).

### The built-in products

Espressif ships 35 files under `device_profiles/espressif/`, one per useful
known device type and named after it (19 RainMaker standard types and 16
extension types), plus `service_layer.yaml`, the shared service layer every
product gets. `esp.device.other` and `esp.device.thread-br` are omitted
because neither has an entity mapping; a real product using the former needs
its own profile. Each shipped file declares exactly one type, so a node of
several types - the esp-rainmaker `multi_device` example, say - needs a
profile that declares those types together; none is shipped, and writing one
is copying the blocks (below). The full list is in the guide under
[Reusing an existing definition](docs/device_profiles_in_detail.md#reusing-an-existing-definition).

The exceptions are `date`, `time` and `datetime`: these are not device types but
entities determined by the parameter structure, so the firmware developer's
profile has to name the parameter.

### Writing a product profile

In three steps: copy the `- type:` block of every device type the node
reports from the shipped file of that type into one file; make the blocks
agree with the firmware; and place the file in `<config>/esp_weaver/`, ready
to pick the next time a device is added or a **Configure** dialog is opened.
The detailed procedure is in
[Developing a product profile](docs/developing_a_product_profile.md#writing-a-profile).

Three rules matter before anything else:

- `type`, `name` and `param` are matched exactly, case included; a mismatch
  raises no error, the entity is silently missing.
- A product profile has to declare **every** device type of the node;
  nothing is filled in from another file.
- `key` must not change once in use, or the entity is replaced and its
  history lost.

Add `vendor`, `product` and `model` at the top of the file, with
`vendor_label` and `product_label` as the display names and
`vendor_labels` / `product_labels` for their translations.
The folder sits outside the integration, so upgrading ESP-Weaver does not
overwrite files placed there; a file left in
`custom_components/esp_weaver/device_profiles/` is listed as well, but is
gone with the next upgrade, and a repair says so. Three ways to get a file
there: copy it in over Samba, SSH or a file editor add-on; upload it on the
first step of **Add integration** → **ESP-Weaver**; or give
`esp_weaver.reload` a `url` to download it from.

### Updates

A device is bound to the content it was confirmed with, so editing a file, or
the repository publishing a new version, changes no device by itself. Where
a product's current content differs from what its devices are bound to, a
repair offers the update for every device on that product together;
confirming applies it, and a single device is moved to another product from
its **Configure** dialog. `esp_weaver.reload` re-reads the folders, refreshes
the repository copy and those repairs, and reloads every device; it is
admin-only. How an edit takes effect, and the common pitfalls, are in the
guide under [How an edit takes effect](docs/device_profiles_in_detail.md#how-an-edit-takes-effect).

Profiles that fail to load are reported as repairs rather than failing setup,
and devices bound to them keep the content they were bound to.

### Further documentation

Two guides serve the two purposes.
[Developing a product profile](docs/developing_a_product_profile.md) covers the
complete process for a firmware developer, from the firmware declarations
to a first working profile and its first verification. [Device profiles in detail](docs/device_profiles_in_detail.md)
specifies how the list is assembled and chosen, how an edit takes effect, the
effect of editing each field on existing entities, several devices and
several files, publishing to the repository, troubleshooting by symptom,
and a complete field reference.

## Known Limitations

- **Discovery is mDNS only.** A device is added from the list the integration
  discovers on the local network (`_esp_local_ctrl._tcp.local.`); there is no
  field for an IP address or host name. Home Assistant and the device have to
  be on the same network segment, or mDNS has to be relayed across them.
- **One product per device.** A device is bound to exactly one profile. A node
  that reports several device types needs a profile declaring all of them
  together; the built-in products each declare one type.
- **Matching is exact.** Device type names and parameter names in the profile
  have to be spelled as the firmware reports them. Nothing is guessed and
  nothing falls back to another file.
- **Only the parameters the firmware reports exist.** An entity whose
  parameter the device did not report is not created; the summary step lists
  them before the device is added.
- **Cloud control is not available in this release.** The RainMaker code stays
  in the repository but is not offered in the UI, so a device is controlled
  over the local network only and needs firmware with local control.
- **Firmware updates and RainMaker scenes are not offered.** The device page
  shows the running firmware version, but installing firmware and the scenes
  stored on the device stay with the RainMaker app; use Home Assistant's own
  scenes instead.

## Troubleshooting

**No ESP devices found on the network.** The device is not advertising
`_esp_local_ctrl._tcp` where Home Assistant can hear it. Check that the
firmware has local control enabled, that the device has joined the Wi-Fi
network, and that it is on the same network segment as Home Assistant (or
that mDNS is relayed between them). Wait a few seconds after the device
boots and open **Add integration** again.

**All discovered devices are already configured.** Every device on the
network already has an entry. To move a device to another product, open its
**Configure** dialog instead of adding it again.

**Invalid PoP code / PoP code is required.** The device runs Sec1 or Sec2
and the proof of possession does not match what the firmware was built with.
The PoP is the one the firmware set, not a Wi-Fi password.

**The device did not say what security it runs** or **did not report what it
is made of.** The device was found but did not answer in time. Nothing was
added; give it a few seconds and add it again.

**The device is unavailable after it was working.** The integration polls
every 60 seconds and reconnects with growing delays (1 s to 30 s) once a
request fails; a device that came back is picked up on the next attempt. If
its IP address changed, the new address is taken from mDNS. A device that
stays unavailable is usually offline or has rebooted into a state where local
control is not running.

**Failed to set up, or the device has no entities.** The profile the device
is bound to could not be read, or none of its parameters match what the
device reports. **Settings** → **System** → **Repairs** says which; the
profile guide covers it in
[Troubleshooting](docs/device_profiles_in_detail.md#troubleshooting).

**A write is refused or fails.** The error message says whether the device
refused the value (the parameter is read-only in the firmware, or the value
is out of range) or could not be reached; the latter clears when the device
is reachable again.

**Verbose logging.** **Settings** → **Tools** → **Actions** →
`logger.set_level` with `custom_components.esp_weaver: debug` takes effect
at once; the `logger:` section of `configuration.yaml` makes it permanent.
The diagnostics download on the device page (**⋮** → **Download
diagnostics**) carries the node's reported structure and the bound profile,
and is the first thing to attach to a bug report.

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
│  iot/client/device_api.py (ESPWeaverApi)                    │
│  └── Main API coordinator, delegates to managers            │
├─────────────────────────────────────────────────────────────┤
│  iot/client/client.py (ESPLocalCtrlClient)                  │
│  └── ESP Local Control protocol                             │
├─────────────────────────────────────────────────────────────┤
│  iot/managers/ (Manager Classes)                            │
│  ├── DeviceRegistry      - Device state registry            │
│  ├── AvailabilityManager - Availability detection           │
│  ├── ConnectionManager   - Connection lifecycle             │
│  ├── PropertyManager     - Property read/write & messaging  │
│  └── DeviceDiscoveryManager - Entity discovery              │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **Coordinator** | `coordinator.py` | Data polling, availability checks, connection monitoring |
| **Entity Base** | `entity.py` | Base entity classes; availability follows the coordinator |
| **ESPWeaverApi** | `iot/client/device_api.py` | Top-level API coordinator |
| **DeviceRegistry** | `iot/managers/device_registry.py` | Centralized device state management |
| **ConnectionManager** | `iot/managers/connection_manager.py` | Connection establishment, reconnection |
| **PropertyManager** | `iot/managers/property_manager.py` | Property operations, event dispatching |
| **DeviceDiscoveryManager** | `iot/managers/entity_discovery.py` | Configuration parsing, entity discovery |
| **ESPLocalCtrlClient** | `iot/client/client.py` | Protocol-level local device communication |

## References

| Resource | Link |
|----------|------|
| ESP-Weaver firmware example | [esp-rainmaker `led_light`](https://github.com/espressif/esp-rainmaker/tree/master/examples/led_light) |
| ESP RainMaker | [github.com/espressif/esp-rainmaker](https://github.com/espressif/esp-rainmaker) |
| ESP Local Control | [ESP-IDF Programming Guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_local_ctrl.html) |
| Home Assistant Dev | [developers.home-assistant.io](https://developers.home-assistant.io/) |
