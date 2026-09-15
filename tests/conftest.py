# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for ESP-Weaver integration tests."""

from collections.abc import Generator, Iterable
from contextlib import suppress
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
import pytest

from custom_components.esp_weaver.const import (
    CONF_CONNECTION_MODE,
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_SECURITY_VERSION,
)
from custom_components.esp_weaver.helpers.once import forget_said
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.iot.managers.device_registry import DeviceInfo
from custom_components.esp_weaver.iot.managers.property_manager import WriteOutcome
from custom_components.esp_weaver.iot.specs.keys import CONNECTION_MODE_LOCAL
from custom_components.esp_weaver.profiles import (
    DeviceIdentity,
    ProductCandidate,
    ProfileBinding,
    catalog as profile_catalog,
    loader as profile_loader,
    mirror as profile_mirror,
    options_with_binding,
)
from custom_components.esp_weaver.profiles.loader import CUSTOM_PROFILE_DIR
from custom_components.esp_weaver.profiles.product import SOURCE_BUILTIN
from custom_components.esp_weaver.profiles.registry import ProfileRegistry
from custom_components.esp_weaver.profiles.schema import (
    parse_profile_document,
    profile_blocks,
)
from custom_components.esp_weaver.profiles.store import async_get_store


# Enable custom integrations for pytest-homeassistant-custom-component
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for all tests."""
    return


@pytest.fixture(autouse=True)
def _every_warning_may_be_said_again() -> None:
    """Forget what the log has said, so no test inherits another's silence.

    Warnings about a profile and firmware disagreeing are said once per
    process; a test asserting one would otherwise fail whenever an earlier
    test had provoked the same one.
    """
    forget_said()


@pytest.fixture(autouse=True)
def mock_zeroconf(hass: HomeAssistant) -> Generator[None]:
    """Mock zeroconf to avoid socket creation in tests."""
    with patch("homeassistant.components.zeroconf.async_setup", return_value=True):
        yield


_SCAN_BUILTIN = profile_loader.scan_builtin


# The product the default test node is bound to. The shipped products declare
# one device type each, and a product fits a node only when the node's types
# are exactly the ones it declares; the default test node has two. This
# product declares those two, with the shipped blocks for them verbatim, and is
# offered as if shipped.
NODE_PRODUCT = "espressif/test_node"
NODE_PRODUCT_LABEL = "Test node"
NODE_PRODUCT_TYPES = ("esp.device.lightbulb", "esp.device.temperature-sensor")
NODE_PRODUCT_LOCATION = "espressif/test_node.yaml"


def _node_product(shipped: list[ProductCandidate], source: str) -> ProductCandidate:
    """Compose the test node's product from the shipped blocks for its types."""
    blocks = {
        block["type"]: block
        for candidate in shipped
        if candidate.content is not None
        for block in candidate.content["devices"]
    }
    document = {
        "version": 1,
        "vendor": profile_loader.BUILTIN_VENDOR,
        "vendor_label": "Espressif",
        "vendor_labels": {"zh-Hans": "乐鑫"},
        "product": NODE_PRODUCT.split("/")[1],
        "product_label": NODE_PRODUCT_LABEL,
        "model": "*",
        "devices": [blocks[device_type] for device_type in NODE_PRODUCT_TYPES],
    }
    return ProductCandidate.from_parsed(
        parse_profile_document(document, NODE_PRODUCT_LOCATION),
        source=source,
        location=NODE_PRODUCT_LOCATION,
    )


def shipped_registry() -> ProfileRegistry:
    """Return a registry of every shipped block, for tests about the mappings.

    No product declares them all; a device is bound to one product. The tests
    that check the shipped mappings themselves, rather than a binding, resolve
    against this. Blocking.
    """
    products, _ = _SCAN_BUILTIN(SOURCE_BUILTIN)
    registry = ProfileRegistry.for_product(
        "shipped", [], [], profile_loader.load_service_layer()
    )
    for candidate in products:
        if candidate.vendor != profile_loader.BUILTIN_VENDOR:
            continue
        assert candidate.content is not None
        devices, services = profile_blocks(candidate.content, candidate.id)
        registry.add_profiles(candidate.id, devices, services)
    return registry


def _scan_shipped_only(source: str) -> tuple[list, list]:
    """Read the integration directory, keeping only what the repository ships.

    The test node's product is added to what is read, as if shipped.
    """
    products, problems = _SCAN_BUILTIN(source)
    shipped = [p for p in products if p.vendor == profile_loader.BUILTIN_VENDOR]
    return (
        [*shipped, _node_product(shipped, source)],
        [p for p in problems if p.kind != profile_loader.PROBLEM_MISPLACED],
    )


@pytest.fixture(autouse=True)
def only_shipped_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offer the products the repository ships, and nothing else.

    A stray file in the integration's directory would otherwise change every
    list and add a config flow step.
    """
    monkeypatch.setattr(profile_catalog, "scan_builtin", _scan_shipped_only)


@pytest.fixture(autouse=True)
def repository_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Have the profile repository be offline, unless a test says otherwise.

    The repository copy is refreshed when a device is added and when profiles
    are reloaded. Tests have no network, and what the repository would list is
    the business of the tests about the repository, which patch this back.
    """

    async def _offline(hass: HomeAssistant) -> tuple[list, str]:
        return [], "offline in tests"

    monkeypatch.setattr(profile_mirror, "async_fetch_index", _offline)


@pytest.fixture
def repository_source_offered() -> Generator[None]:
    """Offer the first step's pull request field, as the flow does once it is on.

    Shipped off, so the tests about following a pull request ask for it;
    every other test runs the flow as the user gets it.
    """
    with patch(
        "custom_components.esp_weaver.flows.flow.REPOSITORY_SOURCE_FIELD_ENABLED",
        True,
    ):
        yield


@pytest.fixture
def stray_profiles_load(
    only_shipped_profiles: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the integration directory for real, for the tests about that.

    Each of them points PROFILE_DIR at a directory it created, so what the
    developer left in the real one still does not reach the assertions.
    """
    monkeypatch.setattr(profile_catalog, "scan_builtin", _SCAN_BUILTIN)


# The test node's product, read once per session. What a test's config entry
# is bound to unless the test says otherwise.
_NODE_PRODUCT: ProductCandidate | None = None


@pytest.fixture(autouse=True)
async def node_product(hass: HomeAssistant) -> ProductCandidate:
    """Read the test node's product, which default test entries are bound to."""
    global _NODE_PRODUCT  # noqa: PLW0603 - a session-wide read of the shipped files
    if _NODE_PRODUCT is None:
        products, _ = await hass.async_add_executor_job(
            _scan_shipped_only, SOURCE_BUILTIN
        )
        _NODE_PRODUCT = next(p for p in products if p.id == NODE_PRODUCT)
    return _NODE_PRODUCT


@pytest.fixture
async def node_product_in_store(
    hass: HomeAssistant, node_product: ProductCandidate
) -> None:
    """Hold the test node's product in the store, so a default entry can set up.

    Not automatic: a stored copy is also a "cached" entry in the product list.
    """
    await (await async_get_store(hass)).async_put(node_product)


def node_binding_options() -> dict[str, Any]:
    """Return the options of an entry bound to the test node's product."""
    assert _NODE_PRODUCT is not None, "needs the node_product_in_store fixture"
    return options_with_binding(None, ProfileBinding.for_candidate(_NODE_PRODUCT))


@pytest.fixture(autouse=True)
def tidy_profile_dir(hass: HomeAssistant) -> Generator[None]:
    """Drop the vendor profile directory the loader creates on first use.

    The test configuration directory is shared and lives inside site-packages,
    so anything left behind there follows the next run around.
    """
    yield
    with suppress(OSError):  # Non-empty means a test put its own files there.
        Path(hass.config.path(CUSTOM_PROFILE_DIR)).rmdir()


@pytest.fixture
def own_config_dir(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the configuration directory at a path this test owns.

    For tests that write profile files: the shared one is reused by every other
    test in the run, and a stray profile there changes what they load.
    """
    monkeypatch.setattr(hass.config, "config_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def socket_enabled(socket_enabled: None) -> None:
    """Enable real sockets, re-exporting ``socket_enabled`` from the HA test plugin."""
    return


# Test constants
TEST_NODE_ID = "test_node_123"
TEST_HOST = "192.168.1.100"
TEST_PORT = 8080
TEST_DEVICE_NAME = "Test ESP Device"
TEST_POP = "test_pop_value"


@pytest.fixture
def mock_config_entry_data() -> dict[str, Any]:
    """Return mock config entry data, as the local config flow writes it."""
    return {
        CONF_HOST: TEST_HOST,
        CONF_PORT: TEST_PORT,
        CONF_NODE_ID: TEST_NODE_ID,
        CONF_CONNECTION_MODE: CONNECTION_MODE_LOCAL,
        CONF_SECURITY_VERSION: 0,
    }


@pytest.fixture
def mock_config_entry_data_with_pop() -> dict[str, Any]:
    """Return mock config entry data with PoP."""
    return {
        CONF_HOST: TEST_HOST,
        CONF_PORT: TEST_PORT,
        CONF_NODE_ID: TEST_NODE_ID,
        CONF_CONNECTION_MODE: CONNECTION_MODE_LOCAL,
        CONF_SECURITY_VERSION: 1,
        CONF_CUSTOM_POP: TEST_POP,
    }


@pytest.fixture
def mock_esp_api() -> Generator[MagicMock]:
    """Create a mock ESP HA API."""
    with patch(
        "custom_components.esp_weaver.iot.client.device_api.ESPWeaverApi"
    ) as mock_api_class:
        mock_api = MagicMock()
        # What the registry holds about the node, as the coordinator reads
        # it back when reconnecting.
        mock_api.get_device = MagicMock(
            return_value=DeviceInfo(node_id=TEST_NODE_ID, ip=TEST_HOST, port=TEST_PORT)
        )
        mock_api.default_port = TEST_PORT
        mock_api.start_services = AsyncMock()
        mock_api.cleanup = AsyncMock()
        mock_api.register_device = AsyncMock(return_value=True)
        mock_api.unregister_device = AsyncMock()
        mock_api.set_connection_mode = MagicMock()
        mock_api.is_device_available_async = AsyncMock(return_value=True)
        mock_api.is_discovery_completed = MagicMock(return_value=False)
        mock_api.mark_discovery_completed = MagicMock()
        mock_api.is_cloud_device = MagicMock(return_value=False)
        mock_api.is_mdns_available = AsyncMock(return_value=True)
        mock_api.parse_and_discover_entities = AsyncMock()
        mock_api.publish_params = AsyncMock()
        mock_api.get_firmware_version = MagicMock(return_value=None)
        mock_api.set_firmware_version = MagicMock()
        mock_api.get_device_data = MagicMock(return_value={})
        mock_api.get_reported_config = MagicMock(return_value=({}, {}))
        # A node that answers, with nothing to report: None would mean it
        # could not be asked, and the coordinator would mark it unavailable.
        mock_api.read_properties = AsyncMock(return_value=[])
        mock_api_class.return_value = mock_api
        yield mock_api


@pytest.fixture
def mock_discovered_devices() -> list[dict[str, Any]]:
    """Return mock discovered devices.

    Both advertise what they are, as current firmware does, so that a flow
    which chose the lightbulb product before looking lists them.
    """
    return [
        {
            "ip": TEST_HOST,
            CONF_NODE_ID: TEST_NODE_ID,
            CONF_PORT: TEST_PORT,
            "device_name": TEST_DEVICE_NAME,
            "model": "led_light",
        },
        {
            "ip": "192.168.1.101",
            CONF_NODE_ID: "test_node_456",
            CONF_PORT: 8080,
            "device_name": "ESP Device 2",
            "model": "led_light",
        },
    ]


@pytest.fixture
def mock_coordinator() -> Generator[MagicMock]:
    """Create a mock coordinator."""
    with patch(
        "custom_components.esp_weaver.coordinator.ESPDataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coord = MagicMock()
        mock_coord.node_id = TEST_NODE_ID
        mock_coord.device_name = TEST_DEVICE_NAME
        mock_coord.identity = DeviceIdentity.of(TEST_NODE_ID, None)
        mock_coord.is_available = True
        mock_coord.last_update_success = True
        mock_coord.api = MagicMock()
        mock_coord.discovered_entities = {
            "sensors": {},
            "binary_sensors": {},
            "lights": {},
            "numbers": {},
        }
        mock_coord.async_config_entry_first_refresh = AsyncMock()
        mock_coord.async_refresh = AsyncMock()
        mock_coord.async_shutdown = AsyncMock()
        # async_add_listener is a sync method that returns a remove callback
        # Must return a callable (not AsyncMock) to avoid unawaited coroutine warnings
        mock_coord.async_add_listener = MagicMock(return_value=MagicMock())
        mock_coordinator_class.return_value = mock_coord
        yield mock_coord


@pytest.fixture
def coordinator(mock_esp_api: MagicMock) -> MagicMock:
    """Create a coordinator for driving one profile-bound entity."""
    coordinator = MagicMock()
    coordinator.node_id = TEST_NODE_ID
    coordinator.device_name = TEST_DEVICE_NAME
    coordinator.identity = DeviceIdentity.of(TEST_NODE_ID, None)
    coordinator.last_update_success = True
    coordinator.is_available = True
    coordinator.discovered_entities = {}
    coordinator.firmware_version = None
    # Entities poll at once when a write goes wrong, to replace what they
    # showed with what the device says.
    coordinator.async_refresh = AsyncMock()
    coordinator.async_resync_structure = AsyncMock()
    mock_esp_api.set_params = AsyncMock(return_value=WriteOutcome.ACCEPTED)
    coordinator.api = mock_esp_api
    return coordinator


@pytest.fixture
def mock_event_dispatcher() -> Generator[MagicMock]:
    """Create a mock event dispatcher."""
    with patch(
        "custom_components.esp_weaver.helpers.event_dispatcher.create_event_dispatcher"
    ) as mock_dispatcher_func:
        mock_dispatcher = MagicMock()
        mock_dispatcher.fire = MagicMock()
        mock_dispatcher.async_fire = AsyncMock()
        mock_dispatcher_func.return_value = mock_dispatcher
        yield mock_dispatcher


# Property response fixtures
@pytest.fixture
def mock_device_properties() -> list[dict[str, Any]]:
    """Return mock device property responses."""
    config_data = {
        "node_id": TEST_NODE_ID,
        "config_version": "1.0",
        "info": {
            "name": TEST_DEVICE_NAME,
            "fw_version": "1.0.0",
            "type": "ESP32",
        },
        "devices": [
            {
                "name": "Light",
                "type": "esp.device.lightbulb",
                "primary": "power",
                "params": [
                    {"name": "power", "type": "esp.param.power", "data_type": "bool"},
                    {
                        "name": "brightness",
                        "type": "esp.param.brightness",
                        "data_type": "int",
                        "bounds": {"min": 0, "max": 100},
                    },
                ],
            },
            {
                "name": "Temperature Sensor",
                "type": "esp.device.temperature-sensor",
                "primary": "temperature",
                "params": [
                    {
                        "name": "temperature",
                        "type": "esp.param.temperature",
                        "data_type": "float",
                    },
                ],
            },
        ],
    }

    params_data = {
        "Light": {
            "power": True,
            "brightness": 80,
        },
        "Temperature Sensor": {
            "temperature": 25.5,
        },
    }

    return [
        {
            "name": "config",
            "type": 1,
            "flags": 0,
            "value": json.dumps(config_data).encode(),
        },
        {
            "name": "params",
            "type": 1,
            "flags": 0,
            "value": json.dumps(params_data).encode(),
        },
    ]


# Common mock fixtures for test classes
@pytest.fixture
def mock_hass() -> MagicMock:
    """Create mock Home Assistant instance with common attributes.

    Note: hass.bus.async_fire is a sync method despite its name,
    so we use MagicMock instead of AsyncMock.
    """
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.config_entries = MagicMock()
    return hass


@pytest.fixture
def mock_registry() -> MagicMock:
    """Create mock device registry."""
    return MagicMock()


@pytest.fixture
def mock_device_entry() -> MagicMock:
    """Create a mock device entry for diagnostics tests."""
    device_entry = MagicMock()
    device_entry.name = TEST_DEVICE_NAME
    device_entry.model = f"ESP-{TEST_NODE_ID}"
    device_entry.manufacturer = "Espressif"
    device_entry.sw_version = "0.0.1"
    device_entry.hw_version = None
    device_entry.identifiers = {(DOMAIN, TEST_NODE_ID)}
    return device_entry


# Test helper functions
class AddedEntities(list[Any]):
    """An ``AddEntitiesCallback`` that keeps the entities a platform adds."""

    def __call__(
        self, new_entities: Iterable[Entity], update_before_add: bool = False
    ) -> None:
        """Record the entities; there is no platform to add them to."""
        self.extend(new_entities)


def record_state_writes(entity: Entity) -> MagicMock:
    """Replace ``entity.async_write_ha_state`` with a mock and return it.

    An entity built outside a platform has no state machine to write to; the
    mock stands in for one and lets a test count the writes.
    """
    write = MagicMock()
    entity.async_write_ha_state = write  # type: ignore[method-assign]
    return write


def state_writes(entity: Entity) -> MagicMock:
    """Return the mock that ``record_state_writes`` put on ``entity``."""
    write = entity.async_write_ha_state
    assert isinstance(write, MagicMock)
    return write


def create_mock_config_entry(
    data: dict[str, Any] | None = None,
    entry_id: str = "test_entry_id",
    title: str = TEST_DEVICE_NAME,
    unique_id: str = TEST_NODE_ID,
) -> MagicMock:
    """Create a mock config entry for testing."""
    if data is None:
        data = {
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_NODE_ID: TEST_NODE_ID,
            CONF_CONNECTION_MODE: CONNECTION_MODE_LOCAL,
            CONF_SECURITY_VERSION: 0,
        }

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = entry_id
    mock_entry.domain = DOMAIN
    mock_entry.title = title
    mock_entry.unique_id = unique_id
    mock_entry.data = data
    mock_entry.options = node_binding_options()
    mock_entry.version = 1
    mock_entry.minor_version = 1
    mock_entry.runtime_data = None
    mock_entry.state = ConfigEntryState.LOADED
    mock_entry.async_on_unload = MagicMock()
    # Read by the coordinator's scheduling, which a listener triggers.
    mock_entry.pref_disable_polling = False
    return mock_entry
