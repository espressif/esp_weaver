# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the profile-driven number platform."""

from collections.abc import Generator, Iterator
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
    NumberDeviceClass,
    NumberMode,
)
from homeassistant.const import STATE_UNKNOWN, EntityCategory, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import pytest

from custom_components.esp_weaver.helpers.signals import signal_binding_discovered
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.number import ESPWeaverNumber, async_setup_entry
from custom_components.esp_weaver.profiles.binding import (
    EntityBinding,
    ParamBinding,
    ThresholdRole,
)
from custom_components.esp_weaver.profiles.schema import AttributeSpec
from tests.conftest import (
    TEST_DEVICE_NAME,
    TEST_NODE_ID,
    AddedEntities,
    create_mock_config_entry,
    record_state_writes,
    state_writes,
)

DEVICE = "Sensor"
TEMP_MIN_PARAM = "temp_min_threshold"


def _binding(
    key: str = "temperature_min_threshold",
    param: str = TEMP_MIN_PARAM,
    bounds: dict[str, Any] | None = None,
    **overrides: Any,
) -> EntityBinding:
    """Build a writable number binding."""
    spec = AttributeSpec(
        name="value",
        param=param,
        data_type="float",
        properties=overrides.pop("properties", ("read", "write")),
        bounds=bounds
        if bounds is not None
        else {"min": -20.0, "max": 40.0, "step": 0.5},
    )
    defaults: dict[str, Any] = {
        "node_id": TEST_NODE_ID,
        "device_name": DEVICE,
        "device_type": "esp.device.sensor",
        "platform": "number",
        "name": "Temperature Min Threshold",
        "key": key,
        "device_class": "temperature",
        "unit": "°C",
        "entity_category": "config",
        "attributes": {
            "value": ParamBinding(device_name=DEVICE, attribute="value", spec=spec)
        },
    }
    return EntityBinding(**{**defaults, **overrides})


def _pressure_binding(**overrides: Any) -> EntityBinding:
    """A pressure threshold, the one unit Home Assistant will not convert."""
    return _binding(
        **{
            "key": "pressure_min_threshold",
            "param": "pressure_min_threshold",
            "bounds": {"min": 900.0, "max": 1000.0, "step": 1.0},
            "device_class": "atmospheric_pressure",
            "unit": "hPa",
            "name": "Pressure Min Threshold",
            **overrides,
        }
    )


@pytest.fixture
def coordinator(hass: HomeAssistant, coordinator: MagicMock) -> MagicMock:
    """Give the shared coordinator a hass, which number writes need."""
    coordinator.hass = hass
    return coordinator


def _number(
    coordinator: MagicMock,
    binding: EntityBinding | None = None,
    report: dict[str, Any] | None = None,
    hass: HomeAssistant | None = None,
) -> ESPWeaverNumber:
    """Build a number entity that is safe to drive without a real platform."""
    entity = ESPWeaverNumber(
        coordinator, binding or _binding(), TEST_DEVICE_NAME, report=report
    )
    if hass is not None:
        entity.hass = hass
    entity.entity_id = "number.test"
    record_state_writes(entity)
    return entity


def _register(hass: HomeAssistant, entity: ESPWeaverNumber) -> None:
    """Put an entity where its pair can find it: in the registry and states."""
    assert entity.unique_id is not None
    entry = er.async_get(hass).async_get_or_create(
        NUMBER_DOMAIN, DOMAIN, entity.unique_id
    )
    value = entity.native_value
    hass.states.async_set(
        entry.entity_id, STATE_UNKNOWN if value is None else str(value)
    )


class TestNumberFromBinding:
    """Building a number out of a resolved binding."""

    def test_takes_its_identity_from_the_binding(self, coordinator: MagicMock) -> None:
        """The unique_id is the one the alert service looks up."""
        entity = _number(coordinator)

        assert entity.unique_id == (f"{TEST_NODE_ID}_temperature_min_threshold")
        assert entity.translation_key == "temperature_min_threshold"
        assert entity.name == "Temperature Min Threshold"

    def test_range_comes_from_the_bounds(self, coordinator: MagicMock) -> None:
        """The profile's bounds, refined by the firmware, become the range."""
        entity = _number(coordinator)

        assert entity.native_min_value == -20.0
        assert entity.native_max_value == 40.0
        assert entity.native_step == 0.5

    def test_declares_its_device_class_and_unit(self, coordinator: MagicMock) -> None:
        """Home Assistant needs both to convert and display the value."""
        entity = _number(coordinator)

        assert entity.device_class == NumberDeviceClass.TEMPERATURE
        assert entity.native_unit_of_measurement == "°C"

    def test_is_a_config_entity_in_box_mode(self, coordinator: MagicMock) -> None:
        """Thresholds are settings, not controls on the main dashboard."""
        entity = _number(coordinator)

        assert entity.entity_category == EntityCategory.CONFIG
        assert entity.mode == NumberMode.BOX

    def test_a_number_without_bounds_keeps_the_defaults(
        self, coordinator: MagicMock
    ) -> None:
        """A profile that declares no range must not crash the entity."""
        entity = _number(coordinator, _binding(bounds={}))

        assert entity.native_value is None

    def test_seeds_itself_from_the_discovery_report(
        self, coordinator: MagicMock
    ) -> None:
        """A new number starts at the value the node already published."""
        entity = _number(coordinator, report={TEMP_MIN_PARAM: 12.5})

        assert entity.native_value == 12.5


class TestNumberReads:
    """Turning device reports into Home Assistant state."""

    def test_adopts_a_reported_value(self, coordinator: MagicMock) -> None:
        """A report naming this parameter updates the entity."""
        entity = _number(coordinator)

        entity._handle_params_update({DEVICE: {TEMP_MIN_PARAM: 5.0}})

        assert entity.native_value == 5.0
        state_writes(entity).assert_called_once()

    def test_a_non_numeric_report_is_refused(
        self, coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Bad firmware data is logged rather than crashing the entity."""
        entity = _number(coordinator)

        entity._handle_params_update({DEVICE: {TEMP_MIN_PARAM: "warm"}})

        assert entity.native_value is None
        assert "non-numeric" in caplog.text

    def test_a_report_for_another_device_is_ignored(
        self, coordinator: MagicMock
    ) -> None:
        """The same parameter name on another device instance is not ours."""
        entity = _number(coordinator)

        entity._handle_params_update({"Other": {TEMP_MIN_PARAM: 5.0}})

        assert entity.native_value is None


class TestNumberWrites:
    """Sending a new value to the device."""

    async def test_writes_the_parameter_the_profile_names(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The payload addresses the device instance, nothing is guessed."""
        entity = _number(coordinator, hass=hass)

        await entity.async_set_native_value(10.0)

        coordinator.api.set_params.assert_awaited_once_with(
            TEST_NODE_ID, {DEVICE: {TEMP_MIN_PARAM: 10.0}}
        )
        assert entity.native_value == 10.0

    async def test_a_failed_write_asks_the_device_what_the_value_is(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A value the device never accepted must not linger in the UI.

        The slider is put right from the device rather than from a remembered
        previous value, because a report may have arrived since.
        """
        entity = _number(coordinator, report={TEMP_MIN_PARAM: 5.0}, hass=hass)
        coordinator.api.set_params = AsyncMock(side_effect=TimeoutError)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_set_native_value(10.0)
        assert err.value.translation_key == "write_failed"

        coordinator.async_refresh.assert_awaited_once()

    async def test_a_read_only_parameter_refuses_the_write(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A parameter the firmware reports as read-only raises instead."""
        entity = _number(coordinator, _binding(properties=("read",)), hass=hass)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_set_native_value(10.0)
        assert err.value.translation_key == "write_not_supported"

        coordinator.api.set_params.assert_not_called()


class TestThresholdPairing:
    """A minimum and its maximum must not cross."""

    def _pair(
        self, coordinator: MagicMock, hass: HomeAssistant
    ) -> tuple[ESPWeaverNumber, ESPWeaverNumber]:
        minimum = _number(
            coordinator,
            _binding(
                threshold=ThresholdRole(
                    "temperature", "min", partner_key="temperature_max_threshold"
                )
            ),
            report={TEMP_MIN_PARAM: 5.0},
            hass=hass,
        )
        maximum = _number(
            coordinator,
            _binding(
                key="temperature_max_threshold",
                param="temp_max_threshold",
                name="Temperature Max Threshold",
                threshold=ThresholdRole(
                    "temperature", "max", partner_key="temperature_min_threshold"
                ),
            ),
            report={"temp_max_threshold": 30.0},
            hass=hass,
        )
        _register(hass, minimum)
        _register(hass, maximum)
        return minimum, maximum

    async def test_a_minimum_cannot_reach_its_maximum(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Setting min above max is refused before anything is sent."""
        minimum, _ = self._pair(coordinator, hass)

        with pytest.raises(ServiceValidationError):
            await minimum.async_set_native_value(35.0)

        coordinator.api.set_params.assert_not_called()

    async def test_a_maximum_cannot_fall_to_its_minimum(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Setting max below min is refused too."""
        _, maximum = self._pair(coordinator, hass)

        with pytest.raises(ServiceValidationError):
            await maximum.async_set_native_value(2.0)

    async def test_a_value_inside_the_pair_is_accepted(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The check only blocks a genuine crossing."""
        minimum, _ = self._pair(coordinator, hass)

        await minimum.async_set_native_value(10.0)

        coordinator.api.set_params.assert_awaited_once()

    async def test_a_lone_threshold_has_nothing_to_cross(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A device reporting only one half is still settable."""
        minimum = _number(
            coordinator,
            _binding(threshold=ThresholdRole("temperature", "min")),
            hass=hass,
        )
        _register(hass, minimum)

        await minimum.async_set_native_value(35.0)

        coordinator.api.set_params.assert_awaited_once()

    async def test_a_plain_setting_is_never_paired(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """Only a number the profile declares as a threshold takes part."""
        entity = _number(
            coordinator,
            _binding(
                key="update_interval",
                param="update_interval",
                bounds={"min": 10, "max": 300, "step": 10},
                name="Update Interval",
                device_class="duration",
                unit="s",
            ),
            hass=hass,
        )

        await entity.async_set_native_value(300.0)

        coordinator.api.set_params.assert_awaited_once()

    async def test_a_partner_without_a_value_does_not_block(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """A partner that is registered but unknown cannot be crossed."""
        minimum, maximum = self._pair(coordinator, hass)
        entry = er.async_get(hass).async_get_entity_id(
            NUMBER_DOMAIN, DOMAIN, maximum.unique_id or ""
        )
        assert entry is not None
        hass.states.async_set(entry, STATE_UNKNOWN)

        await minimum.async_set_native_value(35.0)

        coordinator.api.set_params.assert_awaited_once()


class TestPressureUnits:
    """Home Assistant converts temperature for a number, but not pressure."""

    @pytest.fixture(autouse=True)
    def _units(self, coordinator: MagicMock) -> Generator[None]:
        """Let each test put the household on US customary units."""
        self._hass = coordinator.hass
        yield
        self._hass.config.units = METRIC_SYSTEM

    @contextlib.contextmanager
    def _imperial(self) -> Iterator[None]:
        with patch.object(self._hass.config, "units", new=US_CUSTOMARY_SYSTEM):
            yield

    def test_metric_users_keep_hectopascals(self, coordinator: MagicMock) -> None:
        """Nothing is converted when the device unit is already the display one."""
        entity = _number(coordinator, _pressure_binding())

        assert entity.native_unit_of_measurement == "hPa"
        assert entity.native_min_value == 900.0

    def test_imperial_users_see_inches_of_mercury(self, coordinator: MagicMock) -> None:
        """The whole range moves with the unit, or the slider is unusable."""
        with self._imperial():
            entity = _number(coordinator, _pressure_binding())

        assert entity.native_unit_of_measurement == UnitOfPressure.INHG
        assert entity.native_min_value == pytest.approx(26.58, abs=0.01)
        assert entity.native_max_value == pytest.approx(29.53, abs=0.01)

    def test_reported_pressure_is_converted_for_display(
        self, coordinator: MagicMock
    ) -> None:
        """The device reports hPa whatever the user's units are."""
        with self._imperial():
            entity = _number(
                coordinator,
                _pressure_binding(),
                report={"pressure_min_threshold": 1013.25},
            )

        assert entity.native_value == pytest.approx(29.92, abs=0.01)

    async def test_a_written_pressure_goes_back_as_hectopascals(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The device only understands its own unit."""
        with self._imperial():
            entity = _number(coordinator, _pressure_binding(), hass=hass)

        await entity.async_set_native_value(28.0)

        _, payload = coordinator.api.set_params.await_args.args
        assert payload[DEVICE]["pressure_min_threshold"] == pytest.approx(
            948.2, abs=0.5
        )

    async def test_a_pressure_outside_the_range_is_clamped(
        self, hass: HomeAssistant, coordinator: MagicMock
    ) -> None:
        """The firmware's own bounds are the last word, whatever the unit."""
        with self._imperial():
            entity = _number(coordinator, _pressure_binding(), hass=hass)

        await entity.async_set_native_value(30.0)

        _, payload = coordinator.api.set_params.await_args.args
        assert payload[DEVICE]["pressure_min_threshold"] == 1000.0

    @pytest.mark.parametrize(
        ("unit", "reported", "shown", "written", "sent"),
        [
            ("kPa", 101.325, 29.92, 28.0, 94.82),
            ("mbar", 1013.25, 29.92, 28.0, 948.2),
            ("Pa", 101325.0, 29.92, 28.0, 94816.0),
        ],
        ids=["kPa", "mbar", "Pa"],
    )
    async def test_the_conversion_starts_from_the_profiles_unit(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        unit: str,
        reported: float,
        shown: float,
        written: float,
        sent: float,
    ) -> None:
        """A profile in kilopascals is not read as hectopascals."""
        binding = _pressure_binding(
            unit=unit, bounds={"min": reported / 2, "max": reported * 2}
        )
        with self._imperial():
            entity = _number(
                coordinator,
                binding,
                report={"pressure_min_threshold": reported},
                hass=hass,
            )

        assert entity.native_value == pytest.approx(shown, abs=0.01)

        await entity.async_set_native_value(written)

        _, payload = coordinator.api.set_params.await_args.args
        assert payload[DEVICE]["pressure_min_threshold"] == pytest.approx(
            sent, rel=0.001
        )

    def test_a_pressure_in_a_unit_nobody_can_convert_is_left_alone(
        self, coordinator: MagicMock
    ) -> None:
        """A profile unit the converter does not know is shown as declared."""
        with self._imperial():
            entity = _number(coordinator, _pressure_binding(unit="units"))

        assert entity.native_unit_of_measurement == "units"
        assert entity.native_min_value == 900.0


class TestNumberDiscovery:
    """The platform's binding discovery listener."""

    async def _setup(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> list[Any]:
        entry = create_mock_config_entry(mock_config_entry_data)
        entry.runtime_data = coordinator
        entry.title = TEST_DEVICE_NAME
        added = AddedEntities()

        await async_setup_entry(hass, entry, added)
        return added

    async def test_creates_a_number_for_a_number_binding(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A number binding for this node produces one seeded entity."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _binding(),
            {TEMP_MIN_PARAM: 8.0},
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].native_value == 8.0

    async def test_ignores_bindings_for_other_platforms(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """A sensor binding is not a number."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        async_dispatcher_send(
            hass,
            signal_binding_discovered(TEST_NODE_ID),
            _binding(platform="sensor"),
            {},
        )
        await hass.async_block_till_done()

        assert added == []

    async def test_does_not_create_the_same_number_twice(
        self,
        hass: HomeAssistant,
        coordinator: MagicMock,
        mock_config_entry_data: dict[str, Any],
    ) -> None:
        """Rediscovery after a reconnect must not duplicate entities."""
        added = await self._setup(hass, coordinator, mock_config_entry_data)

        for _ in range(2):
            async_dispatcher_send(
                hass, signal_binding_discovered(TEST_NODE_ID), _binding(), {}
            )
            await hass.async_block_till_done()

        assert len(added) == 1
