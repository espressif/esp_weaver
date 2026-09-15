# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver media player entity, driven entirely by device profiles."""

from typing import Any, Final

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import Platform

from .entity import (
    ATTR_STATE,
    ESPWeaverCompositeEntity,
    command_state,
    resolve_commands,
)
from .helpers.discovery import binding_platform_setup

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_SOURCE: Final = "source"
ATTR_VOLUME: Final = "volume"
ATTR_MUTE: Final = "mute"
ATTR_MEDIA_STATE: Final = "media_state"
ATTR_MEDIA_CONTROL: Final = "media_control"

# The transport commands and the feature each unlocks; a device accepts the
# ones it listed as the parameter's values.
CONTROL_FEATURES: Final[dict[str, MediaPlayerEntityFeature]] = {
    "play": MediaPlayerEntityFeature.PLAY,
    "pause": MediaPlayerEntityFeature.PAUSE,
    "stop": MediaPlayerEntityFeature.STOP,
    "next": MediaPlayerEntityFeature.NEXT_TRACK,
    "previous": MediaPlayerEntityFeature.PREVIOUS_TRACK,
}

# The playback state a command leaves behind; skip commands are left out,
# since only the device knows.
CONTROL_RESULTS: Final[dict[str, MediaPlayerState]] = {
    "play": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
    "stop": MediaPlayerState.IDLE,
}


class ESPWeaverMediaPlayer(ESPWeaverCompositeEntity, MediaPlayerEntity):
    """A speaker or TV whose parameter mapping comes from a device profile.

    Everything past power is optional; the transport buttons are the ones the
    device's own value list mentions.
    """

    _device_class_enum = MediaPlayerDeviceClass

    def _configure(self) -> None:
        """Set up the media player."""
        binding = self._binding
        # Spelled Home Assistant's way; a device that differs declares a map.
        self._controls = resolve_commands(
            binding,
            ATTR_MEDIA_CONTROL,
            {action: action for action in CONTROL_FEATURES},
        )
        self._attr_supported_features = self._resolve_features()

        self._attr_source_list = self._options(ATTR_SOURCE) or None

    def _resolve_features(self) -> MediaPlayerEntityFeature:
        """Offer only the controls the device reported a parameter for."""
        features = MediaPlayerEntityFeature(0)
        if self._has(ATTR_STATE):
            features |= (
                MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
            )
        if self._has(ATTR_VOLUME):
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
            )
        if self._has(ATTR_MUTE):
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self._has(ATTR_SOURCE):
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        for action in self._controls:
            features |= CONTROL_FEATURES[action]
        return features

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt a report, noting a playback state Home Assistant has no name for."""
        return self._apply_checked(
            reported, ATTR_MEDIA_STATE, MediaPlayerState, "media state"
        )

    @property
    def state(self) -> MediaPlayerState | None:
        """Return what the player is doing, with power taking precedence."""
        if self._has(ATTR_STATE) and not self._values.get(ATTR_STATE):
            return MediaPlayerState.OFF

        reported = self._values.get(ATTR_MEDIA_STATE)
        if isinstance(reported, str) and reported in MediaPlayerState:
            return MediaPlayerState(reported)

        return MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        """Return the volume from 0 to 1, if the device has one."""
        return self._value(ATTR_VOLUME, float)

    @property
    def is_volume_muted(self) -> bool | None:
        """Return True if muted, if the device can be muted at all."""
        if not self._has(ATTR_MUTE):
            return None
        return self._value(ATTR_MUTE, bool)

    @property
    def source(self) -> str | None:
        """Return the selected input, if the device has one."""
        return self._value(ATTR_SOURCE, str)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the player on."""
        await self._apply({ATTR_STATE: True})

    async def async_turn_off(self) -> None:
        """Turn the player off."""
        await self._apply({ATTR_STATE: False})

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume."""
        await self._apply({ATTR_VOLUME: volume})

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute."""
        await self._apply({ATTR_MUTE: mute})

    async def async_select_source(self, source: str) -> None:
        """Switch to another input."""
        await self._apply({ATTR_SOURCE: source})

    async def _control(self, action: str) -> None:
        """Send one transport command, spelled the way the device wants it."""
        value = command_state(self._controls, action, self._node_id, self._binding.name)

        # Adopt the resulting state now: the device answers on another
        # parameter, and a local node is only read once a minute.
        result = CONTROL_RESULTS.get(action)
        if result is not None and self._has(ATTR_MEDIA_STATE):
            self._values[ATTR_MEDIA_STATE] = result.value

        await self._write_optimistic(value, ATTR_MEDIA_CONTROL)

    async def async_media_play(self) -> None:
        """Start playback."""
        await self._control("play")

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._control("pause")

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._control("stop")

    async def async_media_next_track(self) -> None:
        """Skip to the next track."""
        await self._control("next")

    async def async_media_previous_track(self) -> None:
        """Go back to the previous track."""
        await self._control("previous")


async_setup_entry = binding_platform_setup(Platform.MEDIA_PLAYER, ESPWeaverMediaPlayer)
