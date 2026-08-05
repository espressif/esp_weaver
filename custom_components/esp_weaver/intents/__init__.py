"""Intents for the ESP-Weaver integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .number_intent import NumberSetValueIntent


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Set up all intents for ESP-Weaver."""
    intent.async_register(hass, NumberSetValueIntent())
