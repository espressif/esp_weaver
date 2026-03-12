# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver light platform."""

from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.esp_weaver.const import LIGHT_EFFECT_MODE_PREFIX, PLATFORM_LIGHT
from custom_components.esp_weaver.iot.specs.events import (
    DOMAIN,
    EVENT_LIGHT_SET_PROPERTIES,
    EVENT_LIGHT_UPDATE,
)
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_NODE_ID,
    KEY_INTENSITY,
    KEY_LIGHT_DATA,
    KEY_LIGHT_MODE,
)
from custom_components.esp_weaver.light import ESPWeaverLight, async_setup_entry

from .conftest import TEST_DEVICE_NAME, TEST_NODE_ID, create_mock_config_entry


@pytest.fixture
def light_with_mock_hass(mock_coordinator: MagicMock) -> ESPWeaverLight:
    """Create a light entity with mocked Home Assistant for turn on/off tests.

    Includes full device capabilities (brightness, hs_color) to test
    optimistic state updates.
    """
    # Provide light_data with all supported features so state updates work
    light = ESPWeaverLight(
        coordinator=mock_coordinator,
        node_id=TEST_NODE_ID,
        device_name=TEST_DEVICE_NAME,
        light_data={
            "power": False,
            "brightness": 100,  # 0-100 ESP scale
            "hue": 0,
            "saturation": 100,
        },
    )

    mock_hass = MagicMock()
    mock_hass.bus = MagicMock()
    mock_hass.bus.async_fire = MagicMock()
    light.hass = mock_hass
    light.async_write_ha_state = MagicMock()

    return light


class TestLightEntity:
    """Test ESPWeaverLight entity."""

    async def test_light_initialization(
        self,
        mock_coordinator: MagicMock,
        mock_light_data: dict[str, Any],
    ) -> None:
        """Test light entity initialization."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data=mock_light_data.get("light_data"),
        )

        assert light._attr_unique_id == f"{DOMAIN}_{TEST_NODE_ID}_{PLATFORM_LIGHT}"
        assert ColorMode.HS in light._attr_supported_color_modes
        # EFFECT feature is only enabled if device provides light_mode
        assert (
            light._attr_supported_features & LightEntityFeature.EFFECT
        ) == LightEntityFeature.EFFECT

    async def test_light_initialization_without_light_mode(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light entity without light_mode support."""
        # Device without light_mode param should not have EFFECT feature
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "brightness": 50},  # No light_mode
        )

        assert light._attr_supported_features == LightEntityFeature(0)
        assert light.effect_list is None
        assert light.effect is None
        assert KEY_LIGHT_MODE not in light.extra_state_attributes

    async def test_light_is_on(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light is_on property."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "brightness": 255},
        )

        assert light.is_on is True

        light._state.is_on = False
        assert light.is_on is False

    async def test_light_brightness(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light brightness property.

        Note: The brightness value is converted through parse_light_params
        which converts ESP brightness (0-100) to HA brightness (0-255).
        """
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "brightness": 50},  # ESP 0-100 scale
        )

        # brightness is from _state which uses parsed values
        assert light.brightness is not None
        assert light.brightness > 0

    async def test_light_hs_color(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light HS color property."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "hue": 180, "saturation": 50},
        )

        hs_color = light.hs_color
        assert isinstance(hs_color, tuple)
        assert len(hs_color) == 2

    async def test_light_effect_list(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light effect list property when device supports light_mode."""
        # Device with light_mode param should have effect_list
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "light_mode": 0},  # Device supports light_mode
        )

        effects = light.effect_list
        assert isinstance(effects, list)
        assert len(effects) > 0

    async def test_light_effect_list_not_supported(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light effect list is None when device doesn't support light_mode."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "brightness": 50},  # No light_mode
        )

        assert light.effect_list is None

    async def test_light_extra_attributes(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test light extra state attributes."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "intensity": 80, "light_mode": 1},
        )

        attrs = light.extra_state_attributes
        assert KEY_INTENSITY in attrs
        assert KEY_LIGHT_MODE in attrs


class TestLightTurnOn:
    """Test light turn on functionality."""

    async def test_turn_on_basic(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test basic turn on."""
        await light_with_mock_hass.async_turn_on()

        assert light_with_mock_hass._state.is_on is True
        light_with_mock_hass.hass.bus.async_fire.assert_called_once()
        call_args = light_with_mock_hass.hass.bus.async_fire.call_args
        assert call_args[0][0] == EVENT_LIGHT_SET_PROPERTIES

    async def test_turn_on_with_brightness(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn on with brightness."""
        await light_with_mock_hass.async_turn_on(**{ATTR_BRIGHTNESS: 200})

        assert light_with_mock_hass._state.is_on is True
        assert light_with_mock_hass._state.brightness == 200
        light_with_mock_hass.hass.bus.async_fire.assert_called_once()

    async def test_turn_on_with_hs_color(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn on with HS color."""
        await light_with_mock_hass.async_turn_on(**{ATTR_HS_COLOR: (180.0, 50.0)})

        assert light_with_mock_hass._state.is_on is True
        assert light_with_mock_hass._state.hs_color == (180.0, 50.0)
        light_with_mock_hass.hass.bus.async_fire.assert_called_once()


class TestLightTurnOff:
    """Test light turn off functionality."""

    async def test_turn_off(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn off."""
        # First turn on
        light_with_mock_hass._state.is_on = True

        await light_with_mock_hass.async_turn_off()

        assert light_with_mock_hass._state.is_on is False
        light_with_mock_hass.hass.bus.async_fire.assert_called_once()
        call_args = light_with_mock_hass.hass.bus.async_fire.call_args
        assert call_args[0][0] == EVENT_LIGHT_SET_PROPERTIES


class TestLightUpdateHandling:
    """Test light update handling."""

    async def test_handle_light_update(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling light update event."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        light.hass = hass
        # Mock async_write_ha_state to avoid entity registration issues
        light.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LIGHT_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "light_data": {
                    "power": True,
                    "brightness": 70,  # ESP scale (0-100)
                },
            },
        )

        light._handle_light_update(event)

        assert light._state.is_on is True
        # brightness will be converted from ESP scale
        light.async_write_ha_state.assert_called_once()

    async def test_ignore_update_for_other_node(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test update for other node is ignored."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": False},
        )
        light.hass = hass
        # Store initial state
        initial_is_on = light._state.is_on

        event = Event(
            event_type=EVENT_LIGHT_UPDATE,
            data={
                CONF_NODE_ID: "different_node",
                "light_data": {
                    "power": True,
                    "brightness": 255,
                },
            },
        )

        light._handle_light_update(event)

        # State should not change
        assert light._state.is_on is initial_is_on


class TestLightPlatformSetup:
    """Test light platform setup."""

    async def test_setup_entry_registers_listeners(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that setup registers discovery and control listeners."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.light.setup_platform_discovery"
            ) as mock_setup,
            patch(
                "custom_components.esp_weaver.light.setup_light_control_listener"
            ) as mock_control,
            patch(
                "custom_components.esp_weaver.light.setup_single_entity_discovery"
            ) as mock_discovery,
        ):
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.coordinator.api = MagicMock()
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

            mock_setup.assert_called_once()
            mock_control.assert_called_once()
            mock_discovery.assert_called_once()


class TestLightUpdateHandlingExtended:
    """Extended tests for light update handling."""

    async def test_handle_light_update_empty_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling light update event with empty light_data."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        light.hass = hass
        light.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LIGHT_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                "light_data": {},  # Empty data
            },
        )

        light._handle_light_update(event)

        # async_write_ha_state should not be called for empty data
        light.async_write_ha_state.assert_not_called()

    async def test_handle_light_update_missing_light_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling light update event with missing light_data key."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        light.hass = hass
        light.async_write_ha_state = MagicMock()

        event = Event(
            event_type=EVENT_LIGHT_UPDATE,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                # No light_data key
            },
        )

        light._handle_light_update(event)

        # async_write_ha_state should not be called
        light.async_write_ha_state.assert_not_called()


class TestLightTurnOnErrors:
    """Test light turn on error handling."""

    async def test_turn_on_with_effect(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn on with effect."""
        # Use a valid effect mode
        effect_name = f"{LIGHT_EFFECT_MODE_PREFIX}1"
        await light_with_mock_hass.async_turn_on(effect=effect_name)

        assert light_with_mock_hass._state.is_on is True
        light_with_mock_hass.hass.bus.async_fire.assert_called_once()


class TestLightPlatformSetupNoResult:
    """Test light platform setup when setup_platform_discovery returns None."""

    async def test_setup_entry_returns_early_when_no_result(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that setup returns early when setup_platform_discovery returns None."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.light.setup_platform_discovery"
            ) as mock_setup,
            patch(
                "custom_components.esp_weaver.light.setup_light_control_listener"
            ) as mock_control,
        ):
            mock_setup.return_value = None

            await async_setup_entry(hass, entry, async_add_entities)

            mock_setup.assert_called_once()
            # Control listener should not be called when result is None
            mock_control.assert_not_called()


class TestLightEntityFactory:
    """Test light entity factory function (line 95)."""

    async def test_create_light_entity_via_factory(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Test that the entity factory creates light entities correctly."""
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = mock_coordinator

        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.esp_weaver.light.setup_platform_discovery"
            ) as mock_setup,
            patch("custom_components.esp_weaver.light.setup_light_control_listener"),
            patch(
                "custom_components.esp_weaver.light.setup_single_entity_discovery"
            ) as mock_discovery,
        ):
            mock_result = MagicMock()
            mock_result.node_id = TEST_NODE_ID
            mock_result.coordinator = mock_coordinator
            mock_result.coordinator.api = MagicMock()
            mock_result.discovered_entities = {}
            mock_setup.return_value = mock_result

            await async_setup_entry(hass, entry, async_add_entities)

            # Get the entity_factory from the call
            call_kwargs = mock_discovery.call_args.kwargs
            entity_factory = call_kwargs.get("entity_factory")
            assert entity_factory is not None

            # Test the factory function
            event_data = {KEY_LIGHT_DATA: {"power": True, "brightness": 100}}
            light = entity_factory(
                event_data, mock_coordinator, TEST_NODE_ID, TEST_DEVICE_NAME
            )

            assert isinstance(light, ESPWeaverLight)
            assert light._node_id == TEST_NODE_ID


class TestLightEffectProperty:
    """Test light effect property (line 193)."""

    async def test_effect_returns_prefixed_mode(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test effect returns correctly prefixed light mode."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        light._state.light_mode = 2

        expected_effect = f"{LIGHT_EFFECT_MODE_PREFIX}2"
        assert light.effect == expected_effect


class TestLightAsyncAddedToHass:
    """Test async_added_to_hass (lines 206-207)."""

    async def test_async_added_to_hass_registers_listener(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test async_added_to_hass registers event listener."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
        )
        light.hass = hass

        # Track async_on_remove calls
        remove_callbacks = []
        light.async_on_remove = lambda cb: remove_callbacks.append(cb)

        await light.async_added_to_hass()

        # Should have registered at least one listener (may include parent class listeners)
        assert len(remove_callbacks) >= 1


class TestLightUpdateEmptyUpdates:
    """Test light update with empty updates (line 239)."""

    async def test_handle_light_update_no_updates(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test handling light update that produces no state changes."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": True, "brightness": 100},
        )
        light.hass = hass
        light.async_write_ha_state = MagicMock()

        # Send update that doesn't change anything
        with patch(
            "custom_components.esp_weaver.light.parse_light_update",
            return_value={},  # Empty updates
        ):
            event = Event(
                event_type=EVENT_LIGHT_UPDATE,
                data={
                    CONF_NODE_ID: TEST_NODE_ID,
                    KEY_LIGHT_DATA: {"power": True},
                },
            )

            light._handle_light_update(event)

            # async_write_ha_state should not be called for empty updates
            light.async_write_ha_state.assert_not_called()


class TestLightTurnOnErrorHandling:
    """Test light turn on error handling (lines 253-255)."""

    async def test_turn_on_value_error(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn on raises HomeAssistantError on ValueError."""
        with patch(
            "custom_components.esp_weaver.light.build_light_turn_on_properties",
            side_effect=ValueError("Invalid brightness value"),
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await light_with_mock_hass.async_turn_on(brightness=300)

            assert "Failed to build light properties" in str(exc_info.value)

    async def test_turn_on_type_error(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn on raises HomeAssistantError on TypeError."""
        with patch(
            "custom_components.esp_weaver.light.build_light_turn_on_properties",
            side_effect=TypeError("Invalid argument type"),
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await light_with_mock_hass.async_turn_on()

            assert "Failed to build light properties" in str(exc_info.value)


class TestLightTurnOffErrorHandling:
    """Test light turn off error handling (lines 286-288)."""

    async def test_turn_off_value_error(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn off raises HomeAssistantError on ValueError."""
        with patch(
            "custom_components.esp_weaver.light.build_light_turn_off_properties",
            side_effect=ValueError("Failed to build properties"),
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await light_with_mock_hass.async_turn_off()

            assert "Failed to build turn off properties" in str(exc_info.value)

    async def test_turn_off_type_error(
        self,
        light_with_mock_hass: ESPWeaverLight,
    ) -> None:
        """Test turn off raises HomeAssistantError on TypeError."""
        with patch(
            "custom_components.esp_weaver.light.build_light_turn_off_properties",
            side_effect=TypeError("Invalid type"),
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await light_with_mock_hass.async_turn_off()

            assert "Failed to build turn off properties" in str(exc_info.value)


class TestLightBrightnessWhenOff:
    """Test brightness property when light is off."""

    async def test_brightness_returns_none_when_off(
        self,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test brightness returns None when light is off."""
        light = ESPWeaverLight(
            coordinator=mock_coordinator,
            node_id=TEST_NODE_ID,
            device_name=TEST_DEVICE_NAME,
            light_data={"power": False, "brightness": 100},
        )
        light._state.is_on = False
        light._state.brightness = 200

        assert light.brightness is None
