# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Every platform, driven the way the integration drives it.

Each platform's ``async_setup_entry`` is run against a node built from the
shipped profiles, which the per-platform tests bypass.
"""

from importlib import import_module
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest

from custom_components.esp_weaver.const import CONF_NODE_ID, PLATFORMS
from custom_components.esp_weaver.helpers.signals import signal_binding_discovered
from custom_components.esp_weaver.profiles import EntityBinding
from custom_components.esp_weaver.profiles.registry import ProfileRegistry
from custom_components.esp_weaver.profiles.resolver import resolve_bindings
from custom_components.esp_weaver.profiles.schema import (
    IMPLEMENTED_PLATFORMS,
    DeviceProfile,
    validate_profile,
)
from tests.conftest import TEST_NODE_ID, AddedEntities, shipped_registry

# Values a parameter has to already hold for the resolver to decide anything
# from it - a device class or an option list. Anything else can be absent,
# which is why the rest are empty.
SAMPLE_VALUES: dict[str, Any] = {
    "string": "",
    "bool": False,
    "int": 0,
    "float": 0.0,
    "object": {},
    "array": [{"id": "1", "name": "First"}, {"id": "2", "name": "Second"}],
}

# Platforms no built-in profile declares: parameter shapes only a vendor
# profile can name. The equivalent of the shipped example is built below.
PARAMETER_SHAPED: frozenset[str] = frozenset({"date", "datetime", "time"})

SCHEDULER_PROFILE: dict[str, Any] = {
    "version": 1,
    "model": "T1",
    "vendor": "acme",
    "product": "thing",
    "devices": [
        {
            "type": "bg.device.scheduler",
            "entities": [
                {
                    "platform": "date",
                    "name": "Holiday Start",
                    "key": "holiday_start",
                    "attributes": {
                        "value": {
                            "param": "Holiday Start",
                            "data_type": "string",
                            "properties": ["read", "write"],
                        }
                    },
                },
                {
                    "platform": "time",
                    "name": "Cycle Start",
                    "key": "cycle_start",
                    "attributes": {
                        "value": {
                            "param": "Cycle Start",
                            "data_type": "string",
                            "properties": ["read", "write"],
                        }
                    },
                },
                {
                    "platform": "datetime",
                    "name": "Next Run",
                    "key": "next_run",
                    "attributes": {
                        "value": {
                            "param": "Next Run",
                            "data_type": "int",
                            "properties": ["read", "write"],
                        }
                    },
                },
            ],
        }
    ],
}

# Values those three need to be given, since "" is not a date and 0 is an
# instant in 1970 rather than a plausible one.
SCHEDULER_VALUES: dict[str, Any] = {
    "Holiday Start": "2026-12-24",
    "Cycle Start": "07:30:00",
    "Next Run": 1798000000,
}


def _registry() -> ProfileRegistry:
    """Return the shipped profiles plus a vendor one for the shapes."""
    registry = shipped_registry()
    validated = validate_profile(SCHEDULER_PROFILE, "scheduler.yaml")
    registry.add_profiles(
        "scheduler.yaml",
        [DeviceProfile.from_dict(d, "scheduler.yaml") for d in validated["devices"]],
        [],
    )
    return registry


def _node_config(registry: ProfileRegistry) -> dict[str, Any]:
    """Build a node declaring every device and service the profiles know."""
    devices: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []

    for types, get, target in (
        (registry.device_types, registry.get_device, devices),
        (registry.service_types, registry.get_service, services),
    ):
        for type_name in types:
            profile = get(type_name)
            assert profile is not None
            block = _device_block(type_name, profile)
            if block["params"]:
                target.append(block)

    return {
        "node_id": TEST_NODE_ID,
        "info": {"name": "Everything"},
        "devices": devices,
        "services": services,
    }


def _device_block(type_name: str, profile: DeviceProfile) -> dict[str, Any]:
    """Return what a node reports for one device, from its profile."""
    params: dict[str, dict[str, Any]] = {}
    for entity in profile.entities:
        if not entity.implemented:
            continue
        for spec in (*entity.attributes.values(), *entity.extra_attributes.values()):
            param: dict[str, Any] = {
                "name": spec.param,
                "data_type": spec.data_type,
                "properties": list(spec.properties or ()),
            }
            if spec.param_type:
                param["type"] = spec.param_type
            if spec.bounds:
                param["bounds"] = dict(spec.bounds)
            if spec.options:
                param["valid_strs"] = list(spec.options)
            params[spec.param] = param

    return {
        "name": type_name.rsplit(".", 1)[-1],
        "type": type_name,
        "params": list(params.values()),
    }


def _params(node_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return a report giving every declared parameter a plausible value."""
    report: dict[str, dict[str, Any]] = {}
    for block in (*node_config["devices"], *node_config["services"]):
        values: dict[str, Any] = {}
        for param in block["params"]:
            listed = param.get("valid_strs")
            values[param["name"]] = SCHEDULER_VALUES.get(
                param["name"],
                listed[0] if listed else SAMPLE_VALUES.get(param["data_type"], 0),
            )
        report[block["name"]] = values
    return report


REGISTRY = _registry()
NODE_CONFIG = _node_config(REGISTRY)
REPORT = _params(NODE_CONFIG)
BINDINGS = resolve_bindings(TEST_NODE_ID, NODE_CONFIG, REPORT, REGISTRY)
BY_PLATFORM: dict[str, list[EntityBinding]] = {}
for binding in BINDINGS:
    BY_PLATFORM.setdefault(binding.platform, []).append(binding)


@pytest.fixture
def entry(coordinator: MagicMock) -> MagicMock:
    """Return a config entry standing in for a set-up node."""
    config_entry = MagicMock()
    config_entry.runtime_data = coordinator
    config_entry.data = {CONF_NODE_ID: TEST_NODE_ID}
    config_entry.title = "Everything"
    config_entry.async_on_unload = MagicMock()
    return config_entry


async def _set_up(
    hass: HomeAssistant, entry: MagicMock, platform: str, added: AddedEntities
) -> None:
    """Set one platform up the way an EntityPlatform would.

    The platform context variable is needed by platforms registering entity actions.
    """
    module = import_module(f"custom_components.esp_weaver.{platform}")
    token = entity_platform.current_platform.set(MagicMock())
    try:
        await module.async_setup_entry(hass, entry, added)
    finally:
        entity_platform.current_platform.reset(token)


class TestPlatformList:
    """The list Home Assistant forwards to, against the list profiles use."""

    def test_the_two_lists_agree(self) -> None:
        """A platform in one and not the other is silently dead either way."""
        assert {platform.value for platform in PLATFORMS} == IMPLEMENTED_PLATFORMS

    def test_every_platform_has_a_module_that_sets_up(self) -> None:
        """Forwarding to a platform with no module fails the whole entry."""
        for platform in PLATFORMS:
            module = import_module(f"custom_components.esp_weaver.{platform.value}")
            assert hasattr(module, "async_setup_entry")

    def test_only_the_parameter_shaped_platforms_need_a_vendor_profile(self) -> None:
        """Every other platform should be reachable out of the box."""
        shipped = shipped_registry()
        declared = {
            entity.platform
            for types, get in (
                (shipped.device_types, shipped.get_device),
                (shipped.service_types, shipped.get_service),
            )
            for type_name in types
            if (profile := get(type_name)) is not None
            for entity in profile.entities
        }

        assert IMPLEMENTED_PLATFORMS - declared == PARAMETER_SHAPED


class TestEveryPlatformBuilds:
    """Each platform, against the profile declarations it was written for."""

    @pytest.mark.parametrize("platform", sorted(IMPLEMENTED_PLATFORMS))
    def test_a_profile_reaches_this_platform(self, platform: str) -> None:
        """A platform nothing resolves to is one no test below can cover."""
        assert BY_PLATFORM.get(platform), platform

    @pytest.mark.parametrize("platform", sorted(IMPLEMENTED_PLATFORMS))
    async def test_the_factory_builds_every_binding(
        self,
        hass: HomeAssistant,
        entry: MagicMock,
        coordinator: MagicMock,
        platform: str,
    ) -> None:
        """One entity per binding, built by the platform's own factory."""
        added = AddedEntities()
        await _set_up(hass, entry, platform, added)

        for binding in BY_PLATFORM[platform]:
            async_dispatcher_send(
                hass, signal_binding_discovered(TEST_NODE_ID), binding, {}
            )
        await hass.async_block_till_done()

        assert len(added) == len(BY_PLATFORM[platform])
        assert {entity.unique_id for entity in added} == {
            f"{TEST_NODE_ID}_{binding.key}" for binding in BY_PLATFORM[platform]
        }

    @pytest.mark.parametrize("platform", sorted(IMPLEMENTED_PLATFORMS))
    async def test_a_seeded_entity_survives_the_reported_values(
        self,
        hass: HomeAssistant,
        entry: MagicMock,
        platform: str,
    ) -> None:
        """Seeding from a typed report must not fail, or the entity is lost."""
        added = AddedEntities()
        await _set_up(hass, entry, platform, added)

        for binding in BY_PLATFORM[platform]:
            async_dispatcher_send(
                hass,
                signal_binding_discovered(TEST_NODE_ID),
                binding,
                REPORT.get(binding.device_name, {}),
            )
        await hass.async_block_till_done()

        assert len(added) == len(BY_PLATFORM[platform])
