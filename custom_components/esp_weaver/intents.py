# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Voice control for what a device profile exposes as a plain number."""

from decimal import Decimal
import logging
from typing import Final

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_STEP,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, intent
import voluptuous as vol

from .identity import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Not ``Hass*``: that prefix is reserved for Home Assistant's built-in intents.
INTENT_NUMBER_SET_VALUE: Final = "EspWeaverNumberSetValue"


def _belongs_to_this_integration(hass: HomeAssistant, entity_id: str) -> bool:
    """Say whether an entity is one of this integration's, by its registry entry."""
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == DOMAIN


def _step_precision(step: float) -> int:
    """Return the number of decimal places needed to represent a step value."""
    exponent = Decimal(str(step)).as_tuple().exponent
    if not isinstance(exponent, int):
        return 0
    return max(0, -exponent)


def _fit_to_entity(value: float, state: State) -> float:
    """Snap a spoken value onto the entity's step; refuse one outside its range.

    Raises:
        IntentHandleError: Out of range. ``IntentHandleError`` takes no
            translation key, so the message is English.
    """
    step = state.attributes.get(ATTR_STEP, 1.0)
    if step > 0:
        value = round(round(value / step) * step, _step_precision(step))
        _LOGGER.debug("Rounded value to step %s: %s", step, value)

    minimum = state.attributes.get(ATTR_MIN, float("-inf"))
    maximum = state.attributes.get(ATTR_MAX, float("inf"))
    if value < minimum or value > maximum:
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "")
        allowed = f"{minimum} to {maximum}" + (f" {unit}" if unit else "")
        raise intent.IntentHandleError(
            f"Value {value} is out of range. Valid range: {allowed}"
        )

    return value


class NumberSetValueIntent(intent.IntentHandler):
    """Set one number entity to one value.

    Named by id, not matched by name: a threshold shares its name with the
    sensor it belongs to.
    """

    intent_type = INTENT_NUMBER_SET_VALUE
    description = "Sets the value of a number entity by entity_id"
    slot_schema = {
        vol.Required("entity_id"): intent.non_empty_string,
        vol.Required("value"): vol.Coerce(float),
    }
    platforms = {NUMBER_DOMAIN}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Set the entity named in the slots, and answer with its new state."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        entity_id: str = slots["entity_id"]["value"]
        value: float = slots["value"]["value"]

        _LOGGER.debug("Setting %s to %s", entity_id, value)

        state = hass.states.get(entity_id)
        if state is None:
            raise intent.IntentHandleError(f"Entity {entity_id} not found")

        if not entity_id.startswith(f"{NUMBER_DOMAIN}."):
            raise intent.IntentHandleError(f"Entity {entity_id} is not a number entity")

        # Only this integration's numbers, and only those exposed to the
        # assistant asking; the built-in intents enforce both through target
        # matching, this one takes an id and has to check explicitly.
        if not _belongs_to_this_integration(hass, entity_id):
            raise intent.IntentHandleError(
                f"Entity {entity_id} is not an {DOMAIN} number entity"
            )
        if intent_obj.assistant and not async_should_expose(
            hass, intent_obj.assistant, entity_id
        ):
            raise intent.IntentHandleError(
                f"Entity {entity_id} is not exposed to {intent_obj.assistant}"
            )

        value = _fit_to_entity(value, state)

        try:
            await hass.services.async_call(
                NUMBER_DOMAIN,
                SERVICE_SET_VALUE,
                service_data={ATTR_VALUE: value},
                target={ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
        except HomeAssistantError as err:
            raise intent.IntentHandleError(
                f"Failed to set {entity_id} to {value}: {err}"
            ) from err

        updated_state = hass.states.get(entity_id)
        response = intent_obj.create_response()
        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    name=state.attributes.get(ATTR_FRIENDLY_NAME, entity_id),
                    id=entity_id,
                )
            ]
        )
        response.async_set_states(matched_states=[updated_state or state])
        return response


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Register every intent this integration answers, once per run."""
    intent.async_register(hass, NumberSetValueIntent())
