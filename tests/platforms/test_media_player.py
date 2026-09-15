# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven media player platform."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.esp_weaver.media_player import ESPWeaverMediaPlayer
from custom_components.esp_weaver.profiles.binding import EntityBinding, ParamBinding
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import TEST_DEVICE_NAME, TEST_NODE_ID

DEVICE = "TV"
POWER = "Power"
VOLUME = "Volume"
MUTE = "Mute"
MEDIA_STATE = "Media State"
MEDIA_CONTROL = "Media Control"
SOURCE = "Input Selector"

# The device counts volume in steps; Home Assistant wants a fraction.
VOLUME_STEPS = 20
SOURCES = ("HDMI1", "HDMI2")
STATE_MAP = {
    "Playing": "playing",
    "Paused": "paused",
    "Stopped": "idle",
    "Idle": "idle",
    "Buffering": "buffering",
}
# The device spells its transport commands with a leading capital; Home Assistant
# calls a service. The profile map is firmware value -> HA value, same as every
# other attribute; writes reverse it.
CONTROL_MAP = {
    "Play": "play",
    "Pause": "pause",
    "Stop": "stop",
    "Next": "next",
    "Previous": "previous",
}


def _param(name: str, spec: AttributeSpec) -> ParamBinding:
    return ParamBinding(device_name=DEVICE, attribute=name, spec=spec)


def _attributes(**present: bool) -> dict[str, ParamBinding]:
    """Build the attribute set of a player, dropping what is asked for."""
    specs = {
        "state": AttributeSpec(
            name="state", param=POWER, data_type="bool", properties=("read", "write")
        ),
        "volume": AttributeSpec(
            name="volume",
            param=VOLUME,
            data_type="float",
            properties=("read", "write"),
            bounds={"min": 0, "max": VOLUME_STEPS, "step": 1},
            scale={"to": [0, 1]},
        ),
        "mute": AttributeSpec(
            name="mute", param=MUTE, data_type="bool", properties=("read", "write")
        ),
        "media_state": AttributeSpec(
            name="media_state",
            param=MEDIA_STATE,
            data_type="string",
            properties=("read", "write"),
            value_map=dict(STATE_MAP),
        ),
        "media_control": AttributeSpec(
            name="media_control",
            param=MEDIA_CONTROL,
            data_type="string",
            properties=("read", "write"),
            value_map=dict(CONTROL_MAP),
        ),
        "source": AttributeSpec(
            name="source",
            param=SOURCE,
            data_type="string",
            properties=("read", "write"),
            options=SOURCES,
        ),
    }
    return {
        name: _param(name, spec)
        for name, spec in specs.items()
        if present.get(name, True)
    }


def _binding(**overrides: Any) -> EntityBinding:
    """Build a media player binding with everything the profile allows."""
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.tv",
        "platform": "media_player",
        "name": "TV",
        "key": "tv",
        "device_class": "tv",
        "attributes": _attributes(),
    }
    return EntityBinding(**{**defaults, **overrides})


def _entity(
    coordinator: MagicMock,
    hass: HomeAssistant | None = None,
    report: dict[str, Any] | None = None,
    **overrides: Any,
) -> ESPWeaverMediaPlayer:
    entity = ESPWeaverMediaPlayer(
        coordinator, _binding(**overrides), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
        entity.entity_id = "media_player.test"
    return entity


class TestMediaPlayerCapabilities:
    """What the player offers depends on the parameters it reported."""

    def test_offers_every_control_it_has_a_parameter_for(
        self, coordinator: MagicMock
    ) -> None:
        """A fully equipped player exposes the whole transport."""
        entity = _entity(coordinator)

        assert entity.supported_features == (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
        )
        assert entity.device_class == MediaPlayerDeviceClass.TV
        assert entity.source_list == list(SOURCES)

    def test_a_power_only_player_offers_nothing_else(
        self, coordinator: MagicMock
    ) -> None:
        """Absent optional parameters must not become dead controls."""
        entity = _entity(
            coordinator,
            attributes=_attributes(
                volume=False,
                mute=False,
                media_state=False,
                media_control=False,
                source=False,
            ),
        )

        assert entity.supported_features == (
            MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
        )
        assert entity.volume_level is None
        assert entity.is_volume_muted is None
        assert entity.source_list is None

    def test_only_the_commands_the_device_lists_are_offered(
        self, coordinator: MagicMock
    ) -> None:
        """Firmware that accepts two commands must not advertise five."""
        attributes = _attributes()
        attributes["media_control"] = _param(
            "media_control",
            AttributeSpec(
                name="media_control",
                param=MEDIA_CONTROL,
                data_type="string",
                properties=("read", "write"),
                options=("play", "pause"),
            ),
        )
        entity = _entity(coordinator, attributes=attributes)

        assert entity.supported_features & MediaPlayerEntityFeature.PLAY
        assert entity.supported_features & MediaPlayerEntityFeature.PAUSE
        assert not entity.supported_features & MediaPlayerEntityFeature.STOP


class TestMediaPlayerReads:
    """Turning a report into a player state."""

    def test_power_off_wins_over_the_media_state(self, coordinator: MagicMock) -> None:
        """A player that is off is off, whatever it was last playing."""
        entity = _entity(coordinator, report={POWER: False, MEDIA_STATE: "Playing"})

        assert entity.state == MediaPlayerState.OFF

    def test_reads_the_mapped_media_state(self, coordinator: MagicMock) -> None:
        """The device's spelling becomes Home Assistant's."""
        entity = _entity(coordinator, report={POWER: True, MEDIA_STATE: "Playing"})

        assert entity.state == MediaPlayerState.PLAYING

    def test_a_stopped_player_is_idle(self, coordinator: MagicMock) -> None:
        """Home Assistant has no stopped state, so stopped is idle."""
        entity = _entity(coordinator, report={POWER: True, MEDIA_STATE: "Stopped"})

        assert entity.state == MediaPlayerState.IDLE

    def test_on_without_a_media_state(self, coordinator: MagicMock) -> None:
        """A player that only reported power is simply on."""
        entity = _entity(coordinator, report={POWER: True})

        assert entity.state == MediaPlayerState.ON

    def test_scales_the_reported_volume(self, coordinator: MagicMock) -> None:
        """Half of twenty steps is half volume, not zero."""
        entity = _entity(coordinator, report={POWER: True, VOLUME: 10})

        assert entity.volume_level == 0.5

    def test_reads_mute_and_source(self, coordinator: MagicMock) -> None:
        """Mute and the selected input come straight off the report."""
        entity = _entity(coordinator, report={POWER: True, MUTE: True, SOURCE: "HDMI2"})

        assert entity.is_volume_muted is True
        assert entity.source == "HDMI2"


class TestMediaPlayerWrites:
    """Driving the player."""

    async def test_set_volume_writes_device_steps(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A fraction becomes the step count the device counts in."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_set_volume_level(0.75)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {VOLUME: 15}}
        )
        assert entity.volume_level == 0.75

    async def test_mute_writes_the_flag(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Muting touches only the mute parameter."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_mute_volume(True)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {MUTE: True}}
        )

    async def test_select_source_writes_it(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The chosen input is passed through as given."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_select_source("HDMI1")

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {SOURCE: "HDMI1"}}
        )

    async def test_play_writes_the_devices_spelling(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The command goes out spelled the way the device wants it."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_media_play()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {MEDIA_CONTROL: "Play"}}
        )

    async def test_play_and_pause_take_effect_before_the_device_answers(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The transport buttons must flip on the press, not at the next poll."""
        entity = _entity(coordinator, hass, report={POWER: True, MEDIA_STATE: "Idle"})

        seen = []
        for command in (
            entity.async_media_play,
            entity.async_media_pause,
            entity.async_media_stop,
        ):
            await command()
            seen.append(entity.state)

        assert seen == [
            MediaPlayerState.PLAYING,
            MediaPlayerState.PAUSED,
            MediaPlayerState.IDLE,
        ]

    async def test_the_device_still_has_the_last_word(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A later report overrides what the command assumed."""
        entity = _entity(coordinator, hass, report={POWER: True, MEDIA_STATE: "Idle"})

        await entity.async_media_play()
        entity._apply_report({MEDIA_STATE: "Buffering"})

        assert entity.state == MediaPlayerState.BUFFERING

    async def test_skipping_leaves_the_state_to_the_device(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Only the device knows whether a skip resumed playback."""
        entity = _entity(coordinator, hass, report={POWER: True, MEDIA_STATE: "Paused"})

        await entity.async_media_next_track()

        assert entity.state == MediaPlayerState.PAUSED

    async def test_a_player_without_a_media_state_still_accepts_commands(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """There is nothing to assume when the device reports no playback state."""
        entity = _entity(
            coordinator,
            hass,
            report={POWER: True},
            attributes=_attributes(media_state=False),
        )

        await entity.async_media_play()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {MEDIA_CONTROL: "Play"}}
        )
        assert entity.state == MediaPlayerState.ON

    async def test_next_track_writes_the_devices_spelling(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Every transport command takes the same route."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_media_next_track()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {MEDIA_CONTROL: "Next"}}
        )

    async def test_a_command_the_device_refuses_is_not_sent(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
    ) -> None:
        """A command outside the device's list must not be invented.

        Said out loud rather than dropped: a request that quietly does nothing
        looks exactly like a device that is not listening.
        """
        attributes = _attributes()
        attributes["media_control"] = _param(
            "media_control",
            AttributeSpec(
                name="media_control",
                param=MEDIA_CONTROL,
                data_type="string",
                properties=("read", "write"),
                options=("play",),
            ),
        )
        entity = _entity(coordinator, hass, attributes=attributes)

        with pytest.raises(ServiceValidationError) as err:
            await entity.async_media_stop()

        assert err.value.translation_key == "command_not_accepted"
        assert err.value.translation_placeholders is not None
        assert err.value.translation_placeholders["command"] == "stop"

        coordinator.api.set_params.assert_not_called()

    async def test_turn_off_writes_power(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Turning off is the power parameter and nothing else."""
        entity = _entity(coordinator, hass, report={POWER: True})

        await entity.async_turn_off()

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {POWER: False}}
        )
        assert entity.state == MediaPlayerState.OFF
