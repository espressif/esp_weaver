# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.2] - 2026-10-15

### Added

- YAML device profiles for mapping RainMaker devices and parameters to Home
  Assistant entities.
- Built-in profiles for 35 device types and support for local, URL-imported,
  and online vendor profiles.
- Twenty-one entity platforms, including climate, cover, fan, lock, media
  player, vacuum, valve, and water heater.
- ESP RainMaker Security2 and standard Security1 local-control compatibility.
- Profile reload, update detection, diagnostics, and repair notifications.
- The `EspWeaverNumberSetValue` voice intent for number entities.

### Changed

- Device setup now requires selecting a product profile. Entries created with
  0.0.1 must be removed and added again.
- Entity unique IDs now use `<node id>_<profile key>`.
- Device names and metadata now follow the connected device and selected
  product.
- Sensor alerts are emitted as `esp_weaver_alert` events for automations.
- Entities become unavailable after the first failed poll.
- The integration is now classified as `local_polling`.
- Simplified Chinese translations were revised.

### Fixed

- Security detection and connection errors during device setup.
- Recovery when a device is temporarily unreachable or changes IP address.
- Entity state after a device rejects a write.
- Removal of stale entities after firmware changes.
- Firmware-version refresh and diagnostics download.
- Atmospheric-pressure unit conversion.
- Repeated connection and profile-mismatch log messages.

## [0.0.1] - 2025-12-31

### Added

- Initial release
- mDNS/Zeroconf automatic device discovery
- ESP Local Control Sec0/Sec1 security protocol support
- Config Flow with device selection and PoP authentication
- Light entity with brightness, HS color, color temperature, and effects
- Sensor entities (temperature, humidity, pressure, etc.)
- Binary sensor entities (door/window, motion, vibration, touch)
- Number entities for threshold configuration
- Battery energy monitoring with charging status
- IMU gesture recognition (toss, flip, shake, rotation)
- Interactive input events and values
- Low power sleep state tracking with wake reason
- HTTP long-polling for real-time state updates
- Automatic reconnection with exponential backoff
- Multi-language support (English, Simplified Chinese)
- Diagnostics support with sensitive data redaction

[0.0.2]: https://github.com/espressif/esp_weaver/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/espressif/esp_weaver/releases/tag/v0.0.1

