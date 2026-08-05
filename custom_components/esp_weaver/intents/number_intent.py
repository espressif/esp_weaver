"""Number entity intent handler for ESP-Weaver integration."""

from __future__ import annotations

from decimal import Decimal
import logging

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

INTENT_NUMBER_SET_VALUE = "HassNumberSetValue"


def _step_precision(step: float) -> int:
    """Return the number of decimal places needed to represent a step value."""
    exponent = Decimal(str(step)).as_tuple().exponent
    if not isinstance(exponent, int):
        return 0
    return max(0, -exponent)


class NumberSetValueIntent(intent.IntentHandler):
    """Handle HassNumberSetValue intents.

    Expects the LLM to provide the exact entity_id (discovered via GetLiveContext)
    and the value to set. HA automatically handles unit conversion (temperature,
    pressure, etc.) so state.attributes already reflect the user's display unit.
    """

    intent_type = INTENT_NUMBER_SET_VALUE
    description = "Sets the value of a number entity by entity_id"
    slot_schema = {
        vol.Required("entity_id"): intent.non_empty_string,
        vol.Required("value"): vol.Coerce(float),
    }
    platforms = {NUMBER_DOMAIN}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
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

        min_value = state.attributes.get("min", float("-inf"))
        max_value = state.attributes.get("max", float("inf"))
        step = state.attributes.get("step", 1.0)
        unit = state.attributes.get("unit_of_measurement", "")

        if step > 0:
            value = round(value / step) * step
            value = round(value, _step_precision(step))
            _LOGGER.debug("Rounded value to step %s: %s", step, value)

        if value < min_value or value > max_value:
            range_msg = f"{min_value} to {max_value}"
            if unit:
                range_msg += f" {unit}"
            raise intent.IntentHandleError(
                f"Value {value} is out of range. Valid range: {range_msg}"
            )

        try:
            await hass.services.async_call(
                NUMBER_DOMAIN,
                "set_value",
                service_data={"value": value},
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
                    name=state.attributes.get("friendly_name", entity_id),
                    id=entity_id,
                )
            ]
        )
        response.async_set_states(matched_states=[updated_state or state])
        return response
