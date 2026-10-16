# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver base entity classes."""

from collections.abc import Callable, Container, Iterable, Mapping
from datetime import date, datetime, time
from enum import Enum, IntFlag, StrEnum
import logging
from typing import TYPE_CHECKING, Any, Final, Generic, TypeVar

from aiohttp import ClientError
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.typing import UndefinedType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .helpers.binding_types import device_class_of, enum_member
from .helpers.signals import signal_params_update
from .identity import DOMAIN, entity_unique_id
from .iot.managers.property_manager import WriteOutcome
from .profiles import EntityBinding, ParamBinding

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .coordinator import ESPDataUpdateCoordinator

# Profile attribute names that more than one platform reads. Names only one
# platform knows about stay in that platform.
ATTR_STATE: Final = "state"
# What a vacuum and a lawn mower carry their state on; an alarm panel uses
# `state` like everything else.
ATTR_ACTIVITY: Final = "activity"
ATTR_CURRENT_TEMPERATURE: Final = "current_temperature"
ATTR_TARGET_TEMPERATURE: Final = "target_temperature"
ATTR_POSITION: Final = "position"

# RainMaker position parameters count up as the cover or valve opens, which is
# Home Assistant's convention too.
POSITION_CLOSED: Final = 0
POSITION_OPEN: Final = 100

_T = TypeVar("_T")
_EnumT = TypeVar("_EnumT", bound=Enum)
_FlagT = TypeVar("_FlagT", bound=IntFlag)
_MomentT = TypeVar("_MomentT", date, time, datetime)


class ESPWeaverBaseEntity(CoordinatorEntity["ESPDataUpdateCoordinator"]):
    """Base entity for ESP-Weaver devices.

    Derives the unique_id from the node and entity key, describes the device
    the same way the config entry registered it, and follows the coordinator
    for availability.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        node_id: str,
        entity_key: str,
        device_name: str | None = None,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self._node_id = node_id
        self._device_name = (
            device_name if device_name is not None else coordinator.device_name
        )
        self._attr_unique_id = entity_unique_id(node_id, entity_key)

        # No model id or firmware version: those are the node's own, and the
        # coordinator writes them from its config; a value here would undo it.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            name=self._device_name,
            serial_number=coordinator.identity.serial_number,
            manufacturer=coordinator.identity.manufacturer,
            model=coordinator.identity.model,
        )

    @property
    def available(self) -> bool:
        """Return True if the device answered its last poll."""
        return self.coordinator.is_available


class ESPWeaverBindingEntity(ESPWeaverBaseEntity):
    """Base for entities whose parameter mapping comes from a device profile.

    Subclasses say how a report becomes their state and, if writable, what a
    command looks like; a platform adds what it needs in :meth:`_configure`.
    """

    # The platform's device class enum, when it has one; the binding's
    # `device_class` is read as a member of it before `_configure` runs.
    _device_class_enum: type[StrEnum] | None = None

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        binding: EntityBinding,
        device_name: str | None,
        report: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the entity from its resolved binding and seed its state."""
        super().__init__(
            coordinator,
            binding.node_id,
            entity_key=binding.key,
            device_name=device_name,
        )
        self._binding = binding
        self._attr_icon = binding.icon
        self._attr_entity_category = enum_member(
            binding, binding.entity_category, EntityCategory
        )
        # The translation keys are the profile keys, so a translated entity
        # keeps its translated name and the rest fall back to the profile's.
        self._attr_translation_key = binding.key
        # Parameters the profile keeps as read-only detail, shown as attributes.
        self._extra: dict[str, Any] = {}
        if self._device_class_enum is not None:
            self._attr_device_class = device_class_of(binding, self._device_class_enum)

        self._configure()
        self._seed(report)

    def _configure(self) -> None:
        """Set up what the platform needs before the first report is applied."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the parameters the profile keeps as read-only detail."""
        return dict(self._extra)

    @property
    def name(self) -> str | UndefinedType | None:
        """Return the translated name, or the one the profile declares.

        Better than Home Assistant's fallback to the device class name.
        """
        platform_data = self.platform_data
        if not platform_data:
            return self._binding.name

        # The key Home Assistant looks entity names up by, spelled out here
        # so that only its public translation table is read.
        key = (
            f"component.{platform_data.platform_name}.entity.{platform_data.domain}"
            f".{self._attr_translation_key}.name"
        )
        return platform_data.platform_translations.get(key) or self._binding.name

    @property
    def suggested_object_id(self) -> str | None:
        """Return the profile's name, so entity_ids stay language neutral."""
        return self._binding.name

    async def async_added_to_hass(self) -> None:
        """Start listening for this device's parameter reports."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_params_update(self._node_id),
                self._handle_params_update,
            )
        )

    @callback
    def _handle_params_update(self, params: Mapping[str, Any]) -> None:
        """Pass this device instance's share of a report to the subclass."""
        reported = params.get(self._binding.device_name)
        if isinstance(reported, dict) and self._adopt_report(reported):
            self.async_write_ha_state()

    def _adopt_report(self, reported: dict[str, Any]) -> bool:
        """Adopt a device report: the read-only detail here, the state by the platform.

        Every platform shows the parameters its profile keeps as
        ``extra_attributes``, whether or not it reads anything else.

        Returns:
            True when an attribute or the state changed.
        """
        changed = self._adopt(self._binding.extra_attributes, reported, self._extra)
        return self._apply_report(reported) or changed

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the state a device report carries, returning True when it changed."""
        raise NotImplementedError

    def _seed(self, report: Mapping[str, Any] | None) -> None:
        """Take the initial state from the report the node already published.

        A fresh entity and a long-running one read a report alike.
        """
        if report:
            self._adopt_report(dict(report))

    def _param_of(self, attribute: str) -> ParamBinding | None:
        """Return the parameter one attribute is bound to, if the device has it."""
        return self._binding.attributes.get(
            attribute
        ) or self._binding.extra_attributes.get(attribute)

    def _mentions(self, attribute: str, reported: dict[str, Any]) -> bool:
        """Return True if a report carries a value for this attribute."""
        param = self._param_of(attribute)
        return param is not None and param.param in reported

    def _read(self, attribute: str, reported: dict[str, Any]) -> Any:
        """Return one attribute's HA-side value from a report, or None."""
        param = self._param_of(attribute)
        if param is None or param.param not in reported:
            return None
        return param.to_ha(reported[param.param])

    def _read_primary(self, reported: dict[str, Any]) -> Any:
        """Return the primary parameter's HA-side value from a report, or None."""
        primary = self._binding.primary
        if primary is None or primary.param not in reported:
            return None
        return primary.to_ha(reported[primary.param])

    def _adopt(
        self,
        attributes: Iterable[str],
        reported: dict[str, Any],
        store: dict[str, Any],
    ) -> bool:
        """Copy the reported values of some attributes into ``store``.

        An attribute the report leaves out is left alone.

        Returns:
            True if any value changed.
        """
        changed = False
        for name in attributes:
            value = self._read(name, reported)
            if value is None or store.get(name) == value:
                continue
            store[name] = value
            changed = True
        return changed

    async def _write(self, value: Any, attribute: str | None = None) -> None:
        """Send one attribute to the device."""
        await self._send(self._binding.command(value, attribute))

    async def _write_optimistic(self, value: Any, attribute: str | None = None) -> None:
        """Show the new value, then send it; ``_send`` rolls back a refused write."""
        self.async_write_ha_state()
        await self._write(value, attribute)

    async def _send(self, payload: dict[str, Any]) -> None:
        """Send a built payload to the device.

        Raises:
            HomeAssistantError: The payload is empty (parameters never reported,
                or read-only), the device refused the write, or the transport
                failed.
        """
        if not payload:
            # Nothing to address means the node and the profile disagree
            # about this entity; the node is read again to settle it.
            await self.coordinator.async_resync_structure()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_not_supported",
                translation_placeholders={
                    "entity": self._binding.name,
                    "node_id": self._node_id,
                },
            )

        placeholders = {"entity": self._binding.name, "node_id": self._node_id}
        try:
            outcome = await self.coordinator.api.set_params(self._node_id, payload)
        except (OSError, ClientError, RuntimeError) as err:
            # The device layer answers rather than raises; this is the net
            # under anything that still gets out.
            await self._resync()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders=placeholders,
            ) from err

        if outcome is WriteOutcome.ACCEPTED:
            return

        await self._resync()
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=(
                "write_failed"
                if outcome is WriteOutcome.UNREACHABLE
                else "write_refused"
            ),
            translation_placeholders=placeholders,
        )

    async def _resync(self) -> None:
        """Ask the device for its real state after an optimistic write went wrong.

        Now, not at the next debounced refresh: the wrong value is showing.
        """
        await self.coordinator.async_refresh()


class ESPWeaverOnOffEntity(ESPWeaverBindingEntity):
    """A binding entity whose primary parameter is read as an on/off value.

    Read-only platforms (binary_sensor) and switching ones (switch, siren)
    adopt the state the same way; only the latter write it back.
    """

    # Declared, not assigned: ToggleEntity and BinarySensorEntity turn
    # `_attr_is_on` into a property that invalidates the cached `is_on`. A
    # class default here would shadow that property in the MRO and reports
    # would go unseen.
    _attr_is_on: bool | None

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the on/off state the device reported."""
        return self._adopt_on_off(reported)

    def _adopt_on_off(self, reported: dict[str, Any]) -> bool:
        """Adopt the primary parameter as on or off, if the report carries it.

        Returns:
            True if the state changed.
        """
        state = self._read_primary(reported)
        if state is None or bool(state) == self._attr_is_on:
            return False
        self._attr_is_on = bool(state)
        return True


class ESPWeaverToggleEntity(ESPWeaverOnOffEntity):
    """A binding entity whose on/off primary parameter can be written."""

    async def _set(self, state: bool) -> None:
        """Send a new state to the device and adopt it optimistically."""
        self._attr_is_on = state
        await self._write_optimistic(state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._set(False)


class ESPWeaverCompositeEntity(ESPWeaverBindingEntity):
    """Base for entities assembled from several parameters at once.

    Subclasses read the latest value of each declared attribute out of
    ``_values``.
    """

    def __init__(
        self,
        coordinator: "ESPDataUpdateCoordinator",
        binding: EntityBinding,
        device_name: str | None,
        report: Mapping[str, Any] | None = None,
    ) -> None:
        """Have the value store ready before the first report is seeded."""
        self._values: dict[str, Any] = {}
        super().__init__(coordinator, binding, device_name, report)

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt every attribute of this entity that the report mentions."""
        return self._adopt(self._binding.attributes, reported, self._values)

    def _apply_checked(
        self, reported: dict[str, Any], attribute: str, known: Container[Any], what: str
    ) -> bool:
        """Adopt a report, warning once if it put an unknown value in ``attribute``."""
        before = self._values.get(attribute)
        changed = ESPWeaverCompositeEntity._apply_report(self, reported)
        value = self._values.get(attribute)
        if value is not None and value != before and value not in known:
            _LOGGER.warning(
                "Device %s reported an unknown %s %r", self._node_id, what, value
            )
        return changed

    def _has(self, attribute: str) -> bool:
        """Return True if the firmware reported this attribute as present."""
        return attribute in self._binding.attributes

    def _options(self, attribute: str) -> list[str]:
        """Return the values the firmware enumerated for an attribute, if any."""
        param = self._binding.attributes.get(attribute)
        return list(param.ha_options or ()) if param else []

    def _value(self, attribute: str, cast: Callable[[Any], _T]) -> _T | None:
        """Return an attribute's last reported value as ``cast`` sees it, or None."""
        value = self._values.get(attribute)
        return None if value is None else cast(value)

    async def _apply(self, values: dict[str, Any]) -> None:
        """Send one combined write and adopt it without waiting for the echo.

        One write, so a fan told to start at a speed never briefly runs at the
        old one. Only attributes the device has a parameter for are adopted;
        a report would never correct the others.

        Raises:
            HomeAssistantError: If the device can take none of it.
        """
        payload = self._binding.commands(values)
        writable = {
            attribute: value
            for attribute, value in values.items()
            if (param := self._binding.attributes.get(attribute)) and param.writable
        }
        self._values.update(writable)
        self.async_write_ha_state()
        await self._send(payload)


class ESPWeaverPositionEntity(ESPWeaverCompositeEntity):
    """A composite entity driven by a single position parameter.

    Cover and valve share the reading and whether the device will take a new
    one; the feature flags stay in each platform because the two use
    different enums for them.
    """

    def _position_param(self) -> ParamBinding | None:
        """Return the position parameter, if the firmware reported one."""
        return self._binding.attributes.get(ATTR_POSITION)

    def _accepts_position(self) -> bool:
        """Return True if the device will be moved, not just read."""
        param = self._position_param()
        return param is not None and param.writable

    @property
    def _position(self) -> int | None:
        """Return how far open the device is, from 0 closed to 100 open."""
        return self._value(ATTR_POSITION, int)


class ESPWeaverThermostatEntity(ESPWeaverCompositeEntity):
    """A composite entity with a temperature reading and a setpoint.

    Climate and water_heater differ in how they fold power and mode together,
    but their temperature half is the same.
    """

    _attr_temperature_unit: str
    _attr_min_temp: float
    _attr_max_temp: float
    _attr_target_temperature_step: float | None

    def _apply_temperature_range(self) -> None:
        """Adopt the profile's unit and the setpoint range the firmware reported."""
        # RainMaker firmware reports Celsius unless the profile says otherwise;
        # Home Assistant converts for display either way.
        self._attr_temperature_unit = self._binding.unit or UnitOfTemperature.CELSIUS
        target = self._binding.attributes.get(ATTR_TARGET_TEMPERATURE)
        if target is None:
            return
        if span := target.span:
            self._attr_min_temp, self._attr_max_temp = span
        bounds = target.spec.bounds or {}
        if (step := bounds.get("step")) is not None:
            self._attr_target_temperature_step = float(step)

    def _with_off(self, modes: list[_T], fallback: _T, off: _T) -> list[_T]:
        """Complete a declared mode list: at least one mode, and off when there is power."""
        if not modes:
            modes.append(fallback)
        if self._has(ATTR_STATE) and off not in modes:
            modes.insert(0, off)
        return modes

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature the device is measuring, if it reports one."""
        return self._value(ATTR_CURRENT_TEMPERATURE, float)

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint, if the device has one."""
        return self._value(ATTR_TARGET_TEMPERATURE, float)


class ESPWeaverPoweredEntity(ESPWeaverCompositeEntity):
    """A composite entity whose profile ``state`` attribute is on/off.

    Light, fan, humidifier and remote derive from ToggleEntity; only the
    reading side is shared, since each carries its own value when turning on.
    """

    @property
    def is_on(self) -> bool | None:
        """Return True if the device is on, None before it has reported."""
        return self._value(ATTR_STATE, bool)


class ESPWeaverActivityEntity(ESPWeaverCompositeEntity):
    """Base for an entity driven by a single enumerated parameter.

    Subclasses call :meth:`_bind_commands`, then use :meth:`_reported` and
    :meth:`_command`.
    """

    _state_attribute: str
    _commands: dict[str, str]

    def _bind_commands(self, attribute: str, wanted: Mapping[str, str]) -> None:
        """Settle which of the wanted commands this device actually accepts."""
        self._state_attribute = attribute
        self._commands = resolve_commands(self._binding, attribute, wanted)

    def _command_features(
        self, table: Mapping[str, tuple[Any, _FlagT]], features: _FlagT
    ) -> _FlagT:
        """Add the feature flag of every accepted command found in ``table``."""
        for command in self._commands:
            if command in table:
                features |= table[command][1]
        return features

    def _reported(self, enum: type[_EnumT]) -> _EnumT | None:
        """Return the reported state as a member of the platform's enum."""
        return reported_member(
            enum,
            self._values.get(self._state_attribute),
            self._node_id,
            self._binding.name,
        )

    async def _command(self, command: str) -> None:
        """Ask the device to move to the state a command stands for."""
        value = command_state(
            self._commands, command, self._node_id, self._binding.name
        )
        await self._apply({self._state_attribute: value})


def resolve_commands(
    binding: EntityBinding, attribute: str, wanted: Mapping[str, str]
) -> dict[str, str]:
    """Return the commands in ``wanted`` that the device has a value for.

    Matching is exact: the values have already been through the profile's
    ``map``, so a difference here is one the profile did not account for, and
    accepting it would hide the profile that needs correcting.
    """
    param = binding.attributes.get(attribute)
    if param is None or not param.writable:
        return {}

    options = param.ha_options
    if not options:
        # Nothing enumerated: assume the device takes every state, spelled the
        # way Home Assistant spells it.
        return dict(wanted)

    listed = set(options)
    return {command: state for command, state in wanted.items() if state in listed}


def reported_member(
    enum: type[_EnumT], value: object, node_id: str, name: str
) -> _EnumT | None:
    """Read one reported string as a member of a Home Assistant enum.

    The value has been through the profile's ``map``, so anything outside the
    enum is read as unknown and logged rather than guessed at: quietly
    correcting it would hide the profile that needs fixing.
    """
    if not isinstance(value, str):
        return None

    try:
        return enum(value)
    except ValueError:
        pass

    _LOGGER.warning(
        "Device %s reported %r for %s, which is not one of %s. If that is how "
        "the device spells it, the profile needs a map for it",
        node_id,
        value,
        name,
        ", ".join(repr(member.value) for member in enum),
    )
    return None


def command_state(
    commands: Mapping[str, str], command: str, node_id: str, name: str
) -> str:
    """Return the state a command asks for, on a device that accepts it.

    Raises:
        ServiceValidationError: The device has no value for this command -
            the action was called directly against an entity that never
            offered it. Raised rather than logged, so it does not look like a
            device that is not listening.
    """
    state = commands.get(command)
    if state is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="command_not_accepted",
            translation_placeholders={
                "entity": name,
                "node_id": node_id,
                "command": command,
                "accepted": ", ".join(sorted(commands.values())) or "nothing",
            },
        )
    return state


class ESPWeaverMomentEntity(ESPWeaverBindingEntity, Generic[_MomentT]):
    """A date, a time of day or an instant held in one device parameter.

    A subclass supplies only the pair of conversions its type needs.
    """

    _attr_native_value: _MomentT | None = None

    def _parse(self, raw: Any) -> _MomentT | None:
        """Read a reported value as this platform's type, or None."""
        raise NotImplementedError

    def _format(self, value: _MomentT) -> Any:
        """Turn a new value into what the device's parameter expects."""
        raise NotImplementedError

    def _apply_report(self, reported: dict[str, Any]) -> bool:
        """Adopt the moment the device reported."""
        raw = self._read_primary(reported)
        if raw is None:
            return False
        value = self._parse(raw)
        if value == self._attr_native_value:
            return False

        self._attr_native_value = value
        return True

    async def async_set_value(self, value: _MomentT) -> None:
        """Send a new value to the device and adopt it optimistically."""
        self._attr_native_value = value
        await self._write_optimistic(self._format(value))
