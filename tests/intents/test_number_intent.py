"""Tests for NumberSetValueIntent handler."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
import pytest

from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.intents import NumberSetValueIntent, _step_precision

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def handler():
    """Return a fresh NumberSetValueIntent instance."""
    return NumberSetValueIntent()


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant with an async service call."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def registry() -> Generator[MagicMock]:
    """The entity registry, listing every entity asked about as one of ours."""
    entry = MagicMock()
    entry.platform = DOMAIN
    with patch("custom_components.esp_weaver.intents.er.async_get") as async_get:
        registry = async_get.return_value
        registry.async_get.return_value = entry
        yield registry


@pytest.fixture
def exposed() -> Generator[MagicMock]:
    """Whether the entity is exposed to the assistant asking: yes, unless told."""
    with patch(
        "custom_components.esp_weaver.intents.async_should_expose", return_value=True
    ) as should_expose:
        yield should_expose


@pytest.fixture(autouse=True)
def _ours_and_exposed(registry: MagicMock, exposed: MagicMock) -> None:
    """Every test below speaks to one of our exposed numbers unless it says otherwise."""


def _make_state(
    entity_id: str,
    value: str = "50",
    *,
    min_val: float = 0,
    max_val: float = 100,
    step: float = 1.0,
    unit: str = "",
    friendly_name: str | None = None,
) -> MagicMock:
    """Create a mock HA State with number-entity attributes."""
    attrs: dict = {
        "min": min_val,
        "max": max_val,
        "step": step,
        "friendly_name": friendly_name
        or entity_id.split(".")[-1].replace("_", " ").title(),
    }
    if unit:
        attrs["unit_of_measurement"] = unit

    state = MagicMock()
    state.entity_id = entity_id
    state.state = value
    state.attributes = attrs
    return state


def _make_intent_obj(hass: MagicMock, entity_id: str, value: float) -> MagicMock:
    """Build a mock Intent object with *entity_id* and *value* slots."""
    obj = MagicMock()
    obj.hass = hass
    obj.assistant = "conversation"
    obj.slots = {
        "entity_id": {"value": entity_id},
        "value": {"value": str(value)},
    }
    response = MagicMock(spec=intent.IntentResponse)
    obj.create_response.return_value = response
    return obj


# ---------------------------------------------------------------------------
# _step_precision helper
# ---------------------------------------------------------------------------


class TestStepPrecision:
    """Tests for _step_precision()."""

    @pytest.mark.parametrize(
        ("step", "expected"),
        [
            (1.0, 1),
            (0.1, 1),
            (0.01, 2),
            (0.5, 1),
            (0.25, 2),
            (5.0, 1),
            (0.001, 3),
        ],
    )
    def test_various_steps(self, step: float, expected: int):
        """Verify decimal precision for different step sizes."""
        assert _step_precision(step) == expected


# ---------------------------------------------------------------------------
# NumberSetValueIntent.async_handle
# ---------------------------------------------------------------------------


class TestNumberSetValueIntent:
    """Tests for async_handle()."""

    # -- Happy path ---------------------------------------------------------

    async def test_normal_set_value(self, handler, mock_hass):
        """Setting a value within range succeeds and calls the service."""
        state = _make_state("number.temperature", "20")
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.temperature", 25)
        result = await handler.async_handle(intent_obj)

        mock_hass.services.async_call.assert_awaited_once_with(
            "number",
            "set_value",
            service_data={"value": 25.0},
            target={"entity_id": "number.temperature"},
            blocking=True,
        )
        result.async_set_results.assert_called_once()
        result.async_set_states.assert_called_once()

    # -- Boundary values ----------------------------------------------------

    async def test_set_min_boundary(self, handler, mock_hass):
        """Setting the minimum allowed value succeeds."""
        state = _make_state("number.volume", "50", min_val=0, max_val=100)
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.volume", 0)
        await handler.async_handle(intent_obj)

        call_args = mock_hass.services.async_call.call_args
        assert call_args.kwargs["service_data"]["value"] == 0.0

    async def test_set_max_boundary(self, handler, mock_hass):
        """Setting the maximum allowed value succeeds."""
        state = _make_state("number.volume", "50", min_val=0, max_val=100)
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.volume", 100)
        await handler.async_handle(intent_obj)

        call_args = mock_hass.services.async_call.call_args
        assert call_args.kwargs["service_data"]["value"] == 100.0

    async def test_value_below_min_raises(self, handler, mock_hass):
        """Value below min raises IntentHandleError and skips service call."""
        state = _make_state("number.volume", "50", min_val=0, max_val=100)
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.volume", -1)
        with pytest.raises(intent.IntentHandleError, match="out of range"):
            await handler.async_handle(intent_obj)

        mock_hass.services.async_call.assert_not_awaited()

    async def test_value_above_max_raises(self, handler, mock_hass):
        """Value above max raises IntentHandleError and skips service call."""
        state = _make_state("number.volume", "50", min_val=0, max_val=100)
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.volume", 101)
        with pytest.raises(intent.IntentHandleError, match="out of range"):
            await handler.async_handle(intent_obj)

        mock_hass.services.async_call.assert_not_awaited()

    async def test_out_of_range_includes_unit(self, handler, mock_hass):
        """Out-of-range error message includes the unit when available."""
        state = _make_state(
            "number.temperature", "20", min_val=15, max_val=30, unit="°C"
        )
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.temperature", 35)
        with pytest.raises(intent.IntentHandleError, match="°C"):
            await handler.async_handle(intent_obj)

    # -- Invalid entity_id --------------------------------------------------

    async def test_entity_not_found_raises(self, handler, mock_hass):
        """Non-existent entity_id raises IntentHandleError."""
        mock_hass.states.get.return_value = None

        intent_obj = _make_intent_obj(mock_hass, "number.nonexistent", 25)
        with pytest.raises(intent.IntentHandleError, match="not found"):
            await handler.async_handle(intent_obj)

    async def test_non_number_entity_raises(self, handler, mock_hass):
        """Entity that is not in the number domain raises IntentHandleError."""
        state = _make_state("sensor.temperature", "20")
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "sensor.temperature", 25)
        with pytest.raises(intent.IntentHandleError, match="not a number entity"):
            await handler.async_handle(intent_obj)

    # -- Whose entity, and whether it is exposed ---------------------------

    async def test_another_integrations_number_is_refused(
        self, handler, mock_hass, registry
    ):
        """The intent answers for this integration's numbers, not for every number."""
        state = _make_state("number.other_volume", "50")
        mock_hass.states.get.return_value = state
        registry.async_get.return_value.platform = "other_integration"

        intent_obj = _make_intent_obj(mock_hass, "number.other_volume", 25)
        with pytest.raises(intent.IntentHandleError, match="not an esp_weaver"):
            await handler.async_handle(intent_obj)

        mock_hass.services.async_call.assert_not_awaited()

    async def test_a_number_without_a_registry_entry_is_refused(
        self, handler, mock_hass, registry
    ):
        """No registry entry, no way to say whose it is: refused."""
        state = _make_state("number.unregistered", "50")
        mock_hass.states.get.return_value = state
        registry.async_get.return_value = None

        intent_obj = _make_intent_obj(mock_hass, "number.unregistered", 25)
        with pytest.raises(intent.IntentHandleError, match="not an esp_weaver"):
            await handler.async_handle(intent_obj)

    async def test_a_number_not_exposed_to_the_assistant_is_refused(
        self, handler, mock_hass, exposed
    ):
        """Exposure is the user's decision, and an id in a slot does not override it."""
        state = _make_state("number.volume", "50")
        mock_hass.states.get.return_value = state
        exposed.return_value = False

        intent_obj = _make_intent_obj(mock_hass, "number.volume", 25)
        with pytest.raises(intent.IntentHandleError, match="not exposed"):
            await handler.async_handle(intent_obj)

        exposed.assert_called_once_with(mock_hass, "conversation", "number.volume")
        mock_hass.services.async_call.assert_not_awaited()

    async def test_without_an_assistant_exposure_is_not_asked_about(
        self, handler, mock_hass, exposed
    ):
        """An intent with no assistant behind it - a script, say - has nothing to expose to."""
        state = _make_state("number.volume", "50")
        mock_hass.states.get.return_value = state
        intent_obj = _make_intent_obj(mock_hass, "number.volume", 25)
        intent_obj.assistant = None

        await handler.async_handle(intent_obj)

        exposed.assert_not_called()
        mock_hass.services.async_call.assert_awaited_once()

    # -- Service call failure -----------------------------------------------

    async def test_service_call_error_wrapped(self, handler, mock_hass):
        """HomeAssistantError from the service call is wrapped in IntentHandleError."""
        state = _make_state("number.temperature", "20")
        mock_hass.states.get.return_value = state
        mock_hass.services.async_call = AsyncMock(
            side_effect=HomeAssistantError("Device offline"),
        )

        intent_obj = _make_intent_obj(mock_hass, "number.temperature", 25)
        with pytest.raises(
            intent.IntentHandleError, match="Failed to set.*Device offline"
        ):
            await handler.async_handle(intent_obj)

    async def test_service_call_error_preserves_cause(self, handler, mock_hass):
        """The original HomeAssistantError is chained via __cause__."""
        state = _make_state("number.temperature", "20")
        mock_hass.states.get.return_value = state
        original = HomeAssistantError("timeout")
        mock_hass.services.async_call = AsyncMock(side_effect=original)

        intent_obj = _make_intent_obj(mock_hass, "number.temperature", 25)
        with pytest.raises(intent.IntentHandleError) as exc_info:
            await handler.async_handle(intent_obj)

        assert exc_info.value.__cause__ is original

    # -- Step rounding ------------------------------------------------------

    async def test_integer_step_rounding(self, handler, mock_hass):
        """Value is snapped to the nearest step (integer step)."""
        state = _make_state("number.brightness", "50", step=5.0)
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.brightness", 23)
        await handler.async_handle(intent_obj)

        call_args = mock_hass.services.async_call.call_args
        assert call_args.kwargs["service_data"]["value"] == 25.0

    async def test_decimal_step_rounding(self, handler, mock_hass):
        """Value is snapped to the nearest decimal step."""
        state = _make_state(
            "number.temperature", "20", min_val=15, max_val=30, step=0.5
        )
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.temperature", 22.3)
        await handler.async_handle(intent_obj)

        call_args = mock_hass.services.async_call.call_args
        assert call_args.kwargs["service_data"]["value"] == 22.5

    # -- Response structure -------------------------------------------------

    async def test_response_has_entity_target(self, handler, mock_hass):
        """Successful response includes the entity as a target."""
        state = _make_state("number.volume", "50", friendly_name="Living Room Volume")
        mock_hass.states.get.return_value = state

        intent_obj = _make_intent_obj(mock_hass, "number.volume", 75)
        result = await handler.async_handle(intent_obj)

        intent_obj.create_response.assert_called_once()
        result.async_set_results.assert_called_once()

        targets = result.async_set_results.call_args.kwargs["success_results"]
        assert len(targets) == 1
        assert targets[0].name == "Living Room Volume"
        assert targets[0].id == "number.volume"
