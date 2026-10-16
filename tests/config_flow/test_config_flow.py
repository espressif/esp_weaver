# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver config flow, driven the way Home Assistant drives it."""

import asyncio
from collections.abc import Generator
import json
import logging
from pathlib import Path
import string
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.exceptions import InvalidTag
from google.protobuf.message import DecodeError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol
import yaml

from custom_components.esp_weaver.const import (
    CLOUD_CONFIG_WAIT_ATTEMPTS,
    CONF_CONNECTION_MODE,
    CONF_CUSTOM_POP,
    CONF_IDENTITY_ADVERTISED,
    CONF_NODE_ID,
    CONF_SECURITY_VERSION,
)
from custom_components.esp_weaver.data import domain_data
from custom_components.esp_weaver.flows import (
    cloud as cloud_module,
    local as local_module,
    provisioning as provisioning_module,
)
from custom_components.esp_weaver.flows.const import (
    ABORT_NO_DEVICES,
    ABORT_NO_FITTING_DEVICES,
    ABORT_NO_NEW_DEVICES,
    ABORT_NODE_UNREAD,
    ABORT_SECURITY_UNKNOWN,
    ABORT_SUMMARY_FAILED,
    CLOUD_ACTION_EXISTING,
    CLOUD_ACTION_PROVISION,
    CLOUD_DEVICE_FETCH_ATTEMPTS,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_DEVICE,
    ERROR_INVALID_POP,
    ERROR_POP_REQUIRED,
    ERROR_PROFILE_FETCH_FAILED,
    ERROR_PROFILE_UNKNOWN,
    ERROR_PROVISIONING_AMBIGUOUS,
    ERROR_PROVISIONING_FAILED,
    ERROR_PROVISIONING_NOT_IN_CLOUD,
    ERROR_RAINMAKER_LOGIN_FAILED,
    ERROR_RAINMAKER_NOT_LOGGED_IN,
    ERROR_REPOSITORY_SOURCE_INVALID,
    ERROR_REPOSITORY_SOURCE_UNREACHABLE,
    ERROR_VENDOR_UNKNOWN,
    ERROR_WIFI_PASSWORD_REQUIRED,
    ERROR_WIFI_SSID_REQUIRED,
    FIELD_ACTION,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_POP,
    FIELD_OVERWRITE,
    FIELD_PROFILE,
    FIELD_PROFILE_FILE,
    FIELD_REPOSITORY_SOURCE,
    FIELD_SELECTED_DEVICE,
    FIELD_VENDOR,
    FIELD_WIFI_PASSWORD,
    FIELD_WIFI_SSID,
    MESSAGE_DEVICE_OFFLINE,
    MESSAGE_DEVICE_ONLINE,
    MESSAGE_MISMATCH_PREFIX,
    MESSAGE_PROFILE_IMPORTED,
    MESSAGE_REPOSITORY_OFFLINE,
    MESSAGE_SOURCE_PREFIX,
    NAV_ACTION_BACK,
    NAV_ACTION_NEXT,
    PROGRESS_PROVISIONING,
    PROVISION_POLL_ATTEMPTS,
    PROVISIONING_ACTION_BACK,
    PROVISIONING_ACTION_RETRY,
    STEP_BLE_SCAN,
    STEP_CLOUD_ACTION_SELECT,
    STEP_CLOUD_DEVICE_SELECT,
    STEP_CONNECTION_MODE,
    STEP_DEVICE_MISMATCH,
    STEP_DEVICE_SETUP,
    STEP_INIT,
    STEP_POP_INPUT,
    STEP_PROFILE_SELECT,
    STEP_PROFILE_VENDOR,
    STEP_PROVISIONING,
    STEP_PROVISIONING_FAILED,
    STEP_RAINMAKER_LOGIN,
    STEP_USER,
    STEP_WIFI_CONFIG,
)
from custom_components.esp_weaver.helpers.translations import COMMON_CATEGORY
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.iot.cloud import CloudDeviceInfo
from custom_components.esp_weaver.iot.discovered_device import DiscoveredDevice
from custom_components.esp_weaver.iot.specs.keys import (
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
)
from custom_components.esp_weaver.profiles import (
    OPTION_PROFILE,
    ProductCandidate,
    ProfileBinding,
    RepositoryRef,
    async_bind,
    async_get_mirror,
    loader as loader_module,
    mirror as mirror_module,
)
from custom_components.esp_weaver.profiles.fit import MISFIT_MODEL
from custom_components.esp_weaver.profiles.loader import (
    CUSTOM_PROFILE_DIR,
    scan_builtin,
    scan_local,
)
from custom_components.esp_weaver.profiles.mirror import CatalogError
from custom_components.esp_weaver.profiles.product import (
    SOURCE_BUILTIN,
    SOURCE_LOCAL,
    SOURCE_ORDER,
    SOURCE_REPO,
)
from custom_components.esp_weaver.profiles.schema import (
    MODEL_GENERIC,
    parse_profile_document,
)
from custom_components.esp_weaver.profiles.selection import update_issue_id
from custom_components.esp_weaver.profiles.store import async_get_store
from tests.conftest import (
    NODE_PRODUCT_LOCATION,
    TEST_DEVICE_NAME,
    TEST_HOST,
    TEST_NODE_ID,
    TEST_POP,
    TEST_PORT,
)
from tests.github_fake import COMMIT, product_doc, serve_ref

# The dropdown offers copies, not ids: the key names the shipped file itself.
SWITCH_PRODUCT = "espressif/switch"
SWITCH_KEY = f"{SOURCE_BUILTIN}:espressif/switch.yaml"
LIGHTBULB_KEY = f"{SOURCE_BUILTIN}:espressif/lightbulb.yaml"
LIGHT_KEY = f"{SOURCE_BUILTIN}:espressif/light.yaml"
NODE_KEY = f"{SOURCE_BUILTIN}:{NODE_PRODUCT_LOCATION}"

# What a switch reports about itself: one device, its one parameter, and the
# model ``*``. The switch product builds one entity from it and skips nothing.
SWITCH_CONFIG: dict[str, Any] = {
    "node_id": TEST_NODE_ID,
    "info": {"model": MODEL_GENERIC},
    "devices": [
        {
            "name": "Switch",
            "type": "esp.device.switch",
            "params": [
                {
                    "name": "Power",
                    "type": "esp.param.power",
                    "data_type": "bool",
                    "properties": ["read", "write"],
                }
            ],
        }
    ],
}
SWITCH_PARAMS: dict[str, Any] = {"Switch": {"Power": True}}

# What the cloud answers about a node whose configuration it has stored. The
# flow waits for this before creating an entry, so anything reaching that point
# has to have it.
CLOUD_NODE_CONFIG = {
    "info": {"model": MODEL_GENERIC},
    "devices": [{"name": "Switch", "type": "esp.device.switch"}],
}
CLOUD_DEVICE = CloudDeviceInfo(node_id="ABC123", name="Test Device", connected=True)

LOGIN = {CONF_EMAIL: "test@example.com", CONF_PASSWORD: "password123"}
WIFI = {
    FIELD_WIFI_SSID: "TestWiFi",
    FIELD_WIFI_PASSWORD: "wifi_pass",
    FIELD_DEVICE_POP: "",
}


def _profile(
    *,
    vendor: str = "acme",
    product: str = "bulb",
    label: str | None = "Bulb",
    model: str = "T1",
    device_type: str = "esp.device.lightbulb",
    entity_name: str = "State",
    param: str = "Power",
) -> str:
    """Return a product as a user would write it: one device, one switch entity.

    The label is left out when None, for a file shown by its id.
    """
    document: dict[str, Any] = {
        "version": 1,
        "model": model,
        "devices": [
            {
                "type": device_type,
                "entities": [
                    {
                        "platform": "switch",
                        "name": entity_name,
                        "key": "state",
                        "attributes": {
                            "state": {
                                "param": param,
                                "data_type": "bool",
                                "properties": ["read", "write"],
                            }
                        },
                    }
                ],
            }
        ],
    }
    document.update({"vendor": vendor, "product": product})
    if label is not None:
        document["product_label"] = label
    return yaml.safe_dump(document)


def _write_profile(config_dir: Path, name: str, text: str) -> None:
    """Drop a profile file where the loader reads the user's own."""
    custom_dir = config_dir / CUSTOM_PROFILE_DIR
    custom_dir.mkdir(exist_ok=True)
    (custom_dir / name).write_text(text, encoding="utf-8")


def _advertised(
    node_id: str = TEST_NODE_ID,
    ip: str = TEST_HOST,
    *,
    name: str | None = TEST_DEVICE_NAME,
    model: str | None = MODEL_GENERIC,
) -> DiscoveredDevice:
    """Return what discovery reports for a device, with the model it advertised.

    A stock device built on Espressif's reference profiles reports ``*`` as
    its model, which is what the shipped products name.
    """
    return DiscoveredDevice(
        ip=ip,
        node_id=node_id,
        port=TEST_PORT,
        device_name=name,
        model=model,
    )


def _properties(
    config: dict[str, Any], params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return the two properties a local control read hands back."""
    return [
        {"name": "config", "value": json.dumps(config).encode()},
        {"name": "params", "value": json.dumps(params or {}).encode()},
    ]


def _options(result: Any) -> dict[str, str]:
    """Return the form's dropdown as value → label."""
    schema = result["data_schema"].schema
    selector_field = next(iter(schema))
    return {
        option["value"]: option["label"]
        for option in schema[selector_field].config["options"]
    }


def _offers_back(result: Any) -> bool:
    """Say whether the form ends with the continue-or-back dropdown, set to continue."""
    schema = result["data_schema"].schema
    field = next((key for key in schema if str(key) == FIELD_ACTION), None)
    if field is None:
        return False
    config = schema[field].config
    return (
        config["options"] == [NAV_ACTION_NEXT, NAV_ACTION_BACK]
        and config["mode"] == "dropdown"
        and field.default() == NAV_ACTION_NEXT
    )


async def _configure(hass: HomeAssistant, result: Any, user_input: Any) -> Any:
    """Answer the form the flow is showing."""
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def _open(hass: HomeAssistant) -> Any:
    """Start the flow, which opens on the upload step."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def _at_vendor(hass: HomeAssistant) -> Any:
    """Start the flow and skip the upload, landing on the vendor list."""
    result = await _configure(hass, await _open(hass), {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == STEP_PROFILE_VENDOR
    return result


async def _pick(hass: HomeAssistant, result: Any, vendor: str, key: str) -> Any:
    """Walk the vendor and product steps from the vendor list."""
    result = await _configure(hass, result, {FIELD_VENDOR: vendor})
    assert result["step_id"] == STEP_PROFILE_SELECT
    return await _configure(hass, result, {FIELD_PROFILE: key})


async def _at_local(
    hass: HomeAssistant, vendor: str = "espressif", key: str = SWITCH_KEY
) -> Any:
    """Start the flow and choose a product, which sends it to the network.

    Every test about reaching a device starts here, and this is what a stock
    device's run looks like: no file of its own, the shipped product, and no
    question about how to reach it while only local control is offered.
    """
    return await _pick(hass, await _at_vendor(hass), vendor, key)


async def _at_connection(
    hass: HomeAssistant, vendor: str = "espressif", key: str = SWITCH_KEY
) -> Any:
    """Choose a product with the cloud on offer, landing on the connection step."""
    result = await _at_local(hass, vendor, key)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == STEP_CONNECTION_MODE
    return result


async def _go_local(hass: HomeAssistant, result: Any) -> Any:
    """Choose the local network from the connection step."""
    return await _configure(hass, result, {CONF_CONNECTION_MODE: CONNECTION_MODE_LOCAL})


async def _at_device_list(
    hass: HomeAssistant, vendor: str = "espressif", key: str = SWITCH_KEY
) -> Any:
    """Run through discovery to the list of devices found."""
    result = await _at_local(hass, vendor, key)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == STEP_DEVICE_SETUP
    return result


async def _select(hass: HomeAssistant, result: Any, node_id: str = TEST_NODE_ID) -> Any:
    """Pick one device from the list."""
    return await _configure(hass, result, {FIELD_SELECTED_DEVICE: node_id})


def _device_labels(result: Any) -> list[str]:
    """Return the device list as the dropdown shows it, in order."""
    field = next(
        key for key in result["data_schema"].schema if str(key) == FIELD_SELECTED_DEVICE
    )
    options = result["data_schema"].schema[field].config["options"]
    return [option["label"] for option in options]


async def _at_cloud_actions(hass: HomeAssistant) -> Any:
    """Choose the cloud and sign in, landing on the choice of what to add."""
    result = await _configure(
        hass, await _at_connection(hass), {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD}
    )
    assert result["step_id"] == STEP_RAINMAKER_LOGIN
    result = await _configure(hass, result, LOGIN)
    assert result["step_id"] == STEP_CLOUD_ACTION_SELECT
    return result


async def _at_wifi(hass: HomeAssistant, device_name: str = "PROV_123456") -> Any:
    """Choose to provision a device and name it, landing on the WiFi form."""
    result = await _configure(
        hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_PROVISION}
    )
    assert result["step_id"] == STEP_BLE_SCAN
    result = await _configure(hass, result, {FIELD_DEVICE_NAME: device_name})
    assert result["step_id"] == STEP_WIFI_CONFIG
    return result


async def _provision(hass: HomeAssistant, result: Any, wifi: Any = WIFI) -> Any:
    """Submit the WiFi form, wait out the provisioning run, and return what followed.

    The run is a task behind a progress dialog; once it is done Home Assistant
    calls the step again, and asking once more is what the frontend does.
    """
    result = await _configure(hass, result, wifi)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == STEP_PROVISIONING
    await hass.async_block_till_done()
    return await _configure(hass, result, None)


async def _retry(hass: HomeAssistant, result: Any) -> Any:
    """Choose retry on the failure form, and wait out the run it starts."""
    result = await _configure(hass, result, {FIELD_ACTION: PROVISIONING_ACTION_RETRY})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await _configure(hass, result, None)


async def _confirm(hass: HomeAssistant, result: Any) -> Any:
    """Finish adding: the connection now creates the entry without a summary."""
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result


def _already_added(hass: HomeAssistant, node_id: str) -> MockConfigEntry:
    """Add an entry for a node, as the flow writes one: the node id in its data."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=node_id, data={CONF_NODE_ID: node_id}
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep a created entry from setting the integration up.

    Home Assistant sets an entry up the moment a flow creates it. That is the
    integration's business, tested with it; here it would only reach for a
    device that is not there.
    """
    with patch(
        "custom_components.esp_weaver.async_setup_entry", return_value=True
    ) as setup:
        yield setup


@pytest.fixture
def cloud_offered() -> Generator[None]:
    """Offer the cloud next to local control, as the flow does once it is turned on.

    Shipped off, so the tests about the cloud path and the choice itself ask
    for it; every other test runs the flow as the user gets it.
    """
    with patch("custom_components.esp_weaver.flows.flow.CLOUD_CONTROL_ENABLED", True):
        yield


@pytest.fixture
def discovered() -> Generator[AsyncMock]:
    """What mDNS turns up. Nothing, until a test says otherwise."""
    with patch(
        "custom_components.esp_weaver.flows.local.async_discover_devices",
        new_callable=AsyncMock,
        return_value=[],
    ) as discover:
        yield discover


@pytest.fixture
def security() -> Generator[MagicMock]:
    """The devices' answers about how they want to be talked to: sec0, PoP ok."""
    with patch(
        "custom_components.esp_weaver.flows.local.ESPSecurityManager"
    ) as manager_class:
        manager = manager_class.return_value
        manager.ask_device_security = AsyncMock(return_value=0)
        manager.test_pop_connection = AsyncMock(return_value=True)
        yield manager


@pytest.fixture
def node() -> Generator[MagicMock]:
    """The device on the other end of the local read. A switch, unless told."""
    with patch(
        "custom_components.esp_weaver.iot.client.node_reader.ESPLocalCtrlClient"
    ) as client_class:
        client = client_class.return_value
        client.connect = AsyncMock(return_value=True)
        client.get_property_values = AsyncMock(
            return_value=_properties(SWITCH_CONFIG, SWITCH_PARAMS)
        )
        client.disconnect = AsyncMock()
        yield client


@pytest.fixture
def one_device(discovered: AsyncMock, security: MagicMock) -> None:
    """One open device on the network, which answers as a switch when read."""
    discovered.return_value = [_advertised()]


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> Generator[MagicMock]:
    """The RainMaker tool: signed in, one device, its config stored.

    The flow waits between its attempts at the cloud, minutes in all for a
    device that never turns up; the waits are made instant rather than the
    attempts fewer, so the counts asserted below are the real ones.
    """
    monkeypatch.setattr(cloud_module, "CLOUD_CONFIG_WAIT_INTERVAL", 0)
    monkeypatch.setattr(cloud_module, "CLOUD_DEVICE_FETCH_INTERVAL", 0)
    monkeypatch.setattr(provisioning_module, "PROVISION_POLL_INTERVAL", 0)
    with patch(
        "custom_components.esp_weaver.flows.cloud.RainMakerCLIClient"
    ) as client_class:
        client = client_class.return_value
        client.check_login_status = AsyncMock(return_value={"logged_in": True})
        client.login = AsyncMock(return_value={"success": True})
        client.logout = AsyncMock()
        client.get_rainmaker_devices = AsyncMock(return_value=[CLOUD_DEVICE])
        client.get_node_details = AsyncMock(return_value=CLOUD_NODE_CONFIG)
        client.provision_device = AsyncMock(
            return_value={"success": True, "node_id": "NEW123"}
        )
        yield client


class TestOpening:
    """The first steps: the upload offer, then the product, then the connection."""

    async def test_the_flow_opens_on_the_profile_step(
        self, hass: HomeAssistant
    ) -> None:
        """A profile has to be in place before the device it describes."""
        result = await _open(hass)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_USER
        assert result["last_step"] is False
        # A profile of the user's own to import, all of it optional. Reading
        # the repository from a pull request is held back in this release.
        assert set(result["data_schema"].schema) == {
            FIELD_PROFILE_FILE,
            FIELD_OVERWRITE,
        }

    @pytest.mark.usefixtures("repository_source_offered")
    async def test_the_pull_request_field_is_offered_once_turned_on(
        self, hass: HomeAssistant
    ) -> None:
        """The field is a switch away, not a rewrite."""
        result = await _open(hass)

        assert set(result["data_schema"].schema) == {
            FIELD_PROFILE_FILE,
            FIELD_OVERWRITE,
            FIELD_REPOSITORY_SOURCE,
        }

    async def test_a_pull_request_sent_while_held_back_is_refused(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """Without the field, a value for it does not reach the step at all."""
        with (
            patch(
                "custom_components.esp_weaver.flows.flow.async_refresh_catalog",
                new_callable=AsyncMock,
            ) as refresh,
            pytest.raises(InvalidData),
        ):
            await _configure(hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"})

        refresh.assert_not_awaited()
        assert (await async_get_mirror(hass)).source is None

    async def test_skipping_it_reaches_the_product_steps(
        self, hass: HomeAssistant, discovered: AsyncMock
    ) -> None:
        """What the device is comes before how to reach it."""
        result = await _at_vendor(hass)

        result = await _pick(hass, result, "espressif", SWITCH_KEY)

        # Picking the product sends the flow to the network at once.
        discovered.assert_awaited_once()
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES

    async def test_a_created_entry_carries_the_flow_version(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
    ) -> None:
        """The version is what a future migration keys on."""
        discovered.return_value = [_advertised()]
        result = await _select(hass, await _at_device_list(hass))

        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["version"] == 1
        assert result["minor_version"] == 1

    async def test_reaching_the_vendor_list_raises_the_update_repairs(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """A newer profile found on the way in is offered, flow finished or not.

        The list is read fresh here; the devices on the old profile are told
        about the new one even if the user closes the dialog at this step.
        """
        _write_profile(own_config_dir, "bulb.yaml", _profile(entity_name="Old"))
        products, _ = scan_local(own_config_dir / CUSTOM_PROFILE_DIR, SOURCE_LOCAL)
        bound = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: TEST_HOST, CONF_PORT: TEST_PORT, CONF_NODE_ID: "n1"},
            unique_id="n1",
        )
        bound.add_to_hass(hass)
        await async_bind(hass, bound, products[0])
        _write_profile(own_config_dir, "bulb.yaml", _profile(entity_name="New"))

        await _at_vendor(hass)

        issue_id = update_issue_id("acme/bulb", SOURCE_LOCAL)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


class TestAProfileAnUpdateWouldDelete:
    """A profile left in the integration's own directory, which updating wipes.

    The repair raised for it carries the warning. The flow does not repeat it:
    the profile works today, and the user came here to add a device.
    """

    @pytest.fixture
    def stray_profile(
        self,
        stray_profiles_load: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Leave a profile where updating the integration would delete it."""
        (tmp_path / "espressif").mkdir()
        (tmp_path / "espressif" / "switch.yaml").write_text(
            _profile(vendor="espressif", product="switch", label=None)
        )
        (tmp_path / "my_light.yaml").write_text(
            _profile(
                vendor="acme",
                product="my_light",
                label=None,
                device_type="esp.device.stray",
            )
        )
        monkeypatch.setattr(loader_module, "PROFILE_DIR", tmp_path)

    async def test_it_does_not_interrupt_adding_a_device(
        self, hass: HomeAssistant, stray_profile: None
    ) -> None:
        """The profile is offered like any other, and the flow carries on."""
        result = await _configure(hass, await _open(hass), {})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROFILE_VENDOR


@pytest.mark.usefixtures("repository_source_offered")
class TestFollowingARepositoryRef:
    """The first step's field naming a pull request to read the repository from.

    The field is held back in this release, so these tests turn it on.
    """

    @pytest.fixture(autouse=True)
    def _config_dir(self, own_config_dir: Path) -> None:
        """Keep the repository copy this test writes to itself."""

    async def test_nothing_typed_leaves_the_published_index_followed(
        self, hass: HomeAssistant
    ) -> None:
        """The plain case costs nothing: no GitHub call, no store write."""
        await _at_vendor(hass)

        assert (await async_get_mirror(hass)).source is None

    async def test_a_pull_request_is_read_and_its_products_listed(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Typing a number lists the pull request's vendor, and the form says so."""
        serve_ref(aioclient_mock, product_doc())

        result = await _configure(
            hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"}
        )

        assert result["step_id"] == STEP_PROFILE_VENDOR
        assert "acme" in _options(result)
        note = result["description_placeholders"]["repository"]
        assert "#123" in note
        assert COMMIT[:7] in note
        # The index, once, on the first step; the vendor step did not ask again.
        assert aioclient_mock.call_count == 1
        assert (await async_get_mirror(hass)).source == "#123"

    async def test_what_is_followed_is_suggested_next_time(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The field shows what the copy follows, so it can be seen and cleared."""
        serve_ref(aioclient_mock, product_doc())
        await _configure(hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"})

        result = await _open(hass)

        [field] = [
            key
            for key in result["data_schema"].schema
            if key == FIELD_REPOSITORY_SOURCE
        ]
        assert field.description == {"suggested_value": "#123"}

    async def test_a_source_followed_but_never_read_says_so(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A copy set to a pull request it could not read yet names the gap."""
        serve_ref(aioclient_mock, published=False)
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))

        # The same source again is not read from the first step; the vendor
        # step tries, and GitHub is not answering.
        result = await _configure(
            hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"}
        )

        assert result["step_id"] == STEP_PROFILE_VENDOR
        note = result["description_placeholders"]["repository"]
        assert "#123" in note
        assert "has not been read successfully" in note

    async def test_clearing_the_field_follows_the_published_index_again(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An empty field is the published repository, whatever was followed."""
        serve_ref(aioclient_mock, product_doc())
        await _configure(hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"})

        result = await _configure(hass, await _open(hass), {})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        assert (await async_get_mirror(hass)).source is None
        assert "#123" not in result["description_placeholders"]["repository"]

    async def test_something_that_is_not_a_ref_is_refused_on_the_field(
        self, hass: HomeAssistant
    ) -> None:
        """A typo is caught before anything is asked of GitHub."""
        result = await _configure(
            hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "acme:add-plug"}
        )

        assert result["step_id"] == STEP_USER
        assert result["errors"] == {
            FIELD_REPOSITORY_SOURCE: ERROR_REPOSITORY_SOURCE_INVALID
        }
        assert (await async_get_mirror(hass)).source is None

    async def test_a_ref_github_cannot_serve_is_refused_and_not_kept(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A wrong number is said so here, and the copy follows what it did."""
        serve_ref(aioclient_mock, product_doc())
        await _configure(hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"})
        serve_ref(aioclient_mock, pull=9, published=False)

        result = await _configure(
            hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "9"}
        )

        assert result["step_id"] == STEP_USER
        assert result["errors"] == {
            FIELD_REPOSITORY_SOURCE: ERROR_REPOSITORY_SOURCE_UNREACHABLE
        }
        placeholders = result["description_placeholders"]
        assert placeholders["source"] == "#9"
        assert "no index on GitHub yet" in placeholders["error"]
        assert (await async_get_mirror(hass)).source == "#123"

    async def test_a_ref_whose_files_ci_would_refuse_is_refused_here(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The vendor reads the same refusal the pull request shows."""
        serve_ref(
            aioclient_mock,
            error="vendors/acme/plug.yaml: label 'Padded ' starts or ends with whitespace",
        )

        result = await _configure(
            hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"}
        )

        assert result["errors"] == {
            FIELD_REPOSITORY_SOURCE: ERROR_REPOSITORY_SOURCE_UNREACHABLE
        }
        assert "whitespace" in result["description_placeholders"]["error"]
        assert (await async_get_mirror(hass)).source is None


class TestChoosingTheProduct:
    """The two steps asking which product the device is: vendor, then product.

    The list is every copy the catalog can offer, nothing in it is preselected,
    and a choice that cannot be fetched is refused on the form rather than
    binding a device to nothing.
    """

    async def test_an_unreachable_repository_is_noted_on_the_form(
        self, hass: HomeAssistant
    ) -> None:
        """A repository that did not answer is said so, not hidden.

        The tests run with the index fetch failing (see conftest), which is
        what an unreachable repository looks like.
        """
        result = await _at_vendor(hass)

        assert result["description_placeholders"]["repository"]

    async def test_the_shipped_products_are_all_offered(
        self, hass: HomeAssistant
    ) -> None:
        """Every product Espressif ships is one the user can pick."""
        result = await _at_vendor(hass)

        vendors = _options(result)
        assert "espressif" in vendors
        # A list of brands, nothing else on the line.
        assert vendors["espressif"] == "Espressif"

        result = await _configure(hass, result, {FIELD_VENDOR: "espressif"})

        assert result["step_id"] == STEP_PROFILE_SELECT
        assert result["description_placeholders"]["vendor"] == "Espressif"
        offered = _options(result)
        assert {
            f"{SOURCE_BUILTIN}:espressif/{product}.yaml"
            for product in (
                "switch",
                "lightbulb",
                "humidifier",
                "fan",
                "temperature_sensor",
            )
        } <= set(offered)

    async def test_a_vendors_products_are_named_and_ordered_by_kind(
        self, hass: HomeAssistant
    ) -> None:
        """Each line is what is on the box: the product's name and its model."""
        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )

        offered = _options(result)
        assert offered[f"{SOURCE_BUILTIN}:espressif/lightbulb.yaml"] == "Lightbulb-*"
        assert offered[f"{SOURCE_BUILTIN}:espressif/humidifier.yaml"] == "Humidifier-*"
        # The vendor was the previous step, and the device type and the group
        # are not the user's concern: none of them is on the line.
        assert not offered[SWITCH_KEY].startswith("Espressif")
        assert "esp.device" not in offered[SWITCH_KEY]
        assert "·" not in offered[SWITCH_KEY]
        # Still ordered by kind, so like products sit together: lighting before
        # switches.
        keys = list(offered)
        assert keys.index(f"{SOURCE_BUILTIN}:espressif/lightbulb.yaml") < keys.index(
            SWITCH_KEY
        )

    async def test_product_labels_follow_home_assistant_language(
        self, hass: HomeAssistant
    ) -> None:
        """A localized label changes the list only, while the model stays literal."""
        hass.config.language = "zh-Hans"

        result = await _at_vendor(hass)
        assert _options(result)["espressif"] == "乐鑫"
        result = await _configure(hass, result, {FIELD_VENDOR: "espressif"})

        offered = _options(result)
        assert result["description_placeholders"]["vendor"] == "乐鑫"
        assert offered[f"{SOURCE_BUILTIN}:espressif/lightbulb.yaml"] == "灯泡-*"
        assert offered[f"{SOURCE_BUILTIN}:espressif/switch.yaml"] == "开关-*"

    async def test_a_vendor_not_in_the_list_is_refused(
        self, hass: HomeAssistant
    ) -> None:
        """Typing filters the vendor list; it does not invent a vendor."""
        result = await _configure(hass, await _at_vendor(hass), {FIELD_VENDOR: "acme"})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        assert result["errors"] == {"base": ERROR_VENDOR_UNKNOWN}

    async def test_nothing_is_preselected(self, hass: HomeAssistant) -> None:
        """Not a shipped product, not what the firmware calls itself, no vendor."""
        result = await _at_vendor(hass)
        vendor_field = next(iter(result["data_schema"].schema))
        assert vendor_field.default is vol.UNDEFINED

        result = await _configure(hass, result, {FIELD_VENDOR: "espressif"})

        selector_field = next(iter(result["data_schema"].schema))
        assert selector_field.default is vol.UNDEFINED

    async def test_the_list_can_be_filtered_by_typing(
        self, hass: HomeAssistant
    ) -> None:
        """Dozens of entries need a filter; HA only renders one with custom_value.

        Text that matches no entry arrives as an unknown key and is refused,
        so typing cannot bind a device to a product that is not in the list.
        """
        result = await _at_vendor(hass)
        schema = result["data_schema"].schema
        assert schema[next(iter(schema))].config["custom_value"] is True

        result = await _configure(hass, result, {FIELD_VENDOR: "espressif"})
        schema = result["data_schema"].schema
        assert schema[next(iter(schema))].config["custom_value"] is True

        result = await _configure(hass, result, {FIELD_PROFILE: "lightbu"})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROFILE_SELECT
        assert result["errors"] == {"base": ERROR_PROFILE_UNKNOWN}

    async def test_two_copies_of_one_product_are_both_offered(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """Nothing is deduplicated; each copy says where it is from, in a word."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        switch = next(p for p in products if p.id == "espressif/switch")
        assert switch.content is not None
        _write_profile(
            own_config_dir,
            "switch.yaml",
            yaml.safe_dump({**switch.content, "product_label": "My switch"}),
        )

        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )

        offered = _options(result)
        # The user picks between the two; no path is needed to tell them apart.
        assert offered["local:switch.yaml"] == "My switch-*  [custom file]"
        assert offered[SWITCH_KEY] == "Switch-*  [built-in]"
        # A product held in one place is not marked, whatever the others do.
        assert offered[LIGHTBULB_KEY] == "Lightbulb-*"

    async def test_one_source_for_the_whole_vendor_is_not_repeated(
        self, hass: HomeAssistant
    ) -> None:
        """Thirty-nine lines all saying "built-in" say nothing."""
        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )

        offered = _options(result)
        assert offered[SWITCH_KEY] == "Switch-*"
        assert not any("[" in label for label in offered.values())

    async def test_an_identical_copy_is_not_a_second_line(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """The same bytes in two places are one product; there is nothing to pick."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        switch = next(p for p in products if p.id == "espressif/switch")
        assert switch.path is not None
        _write_profile(own_config_dir, "switch.yaml", switch.path.read_text())

        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )

        offered = _options(result)
        # The user's folder comes first in list order, so its copy stands.
        assert offered["local:switch.yaml"] == "Switch-*"
        assert SWITCH_KEY not in offered
        assert not any("[" in label for label in offered.values())

    async def test_the_copy_kept_from_the_first_device_hides_behind_its_original(
        self, hass: HomeAssistant
    ) -> None:
        """After one device is added, the list looks as before."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        lightbulb = next(p for p in products if p.id == "espressif/lightbulb")
        await (await async_get_store(hass)).async_put(lightbulb)

        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )

        offered = _options(result)
        assert offered[f"{SOURCE_BUILTIN}:espressif/lightbulb.yaml"] == "Lightbulb-*"
        assert f"cached:espressif/lightbulb@{lightbulb.hash}" not in offered
        assert not any("[" in label for label in offered.values())

    async def test_a_kept_copy_that_differs_is_a_second_line(
        self, hass: HomeAssistant
    ) -> None:
        """When the original moved on, the copy a device is bound to is a choice."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        lightbulb = next(p for p in products if p.id == "espressif/lightbulb")
        assert lightbulb.content is not None
        older = ProductCandidate.from_parsed(
            parse_profile_document(
                {**lightbulb.content, "product_label": "Lightbulb, as bound"},
                lightbulb.location,
            ),
            source=lightbulb.source,
            location=lightbulb.location,
        )
        await (await async_get_store(hass)).async_put(older)

        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )

        offered = _options(result)
        assert offered[f"{SOURCE_BUILTIN}:espressif/lightbulb.yaml"] == (
            "Lightbulb-*  [built-in]"
        )
        # No hash: the word is enough to pick by, and the hash says nothing.
        assert offered[f"cached:espressif/lightbulb@{older.hash}"] == (
            "Lightbulb, as bound-*  [saved]"
        )
        assert offered[SWITCH_KEY] == "Switch-*"

    async def test_the_users_own_files_are_named_by_file(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """Two files describing the same product differently are told apart by nothing else."""
        for name in ("hall", "porch"):
            _write_profile(
                own_config_dir,
                f"{name}.yaml",
                _profile(vendor="acme", product="light", label=None, entity_name=name),
            )

        result = await _configure(hass, await _at_vendor(hass), {FIELD_VENDOR: "acme"})

        offered = _options(result)
        # Both are custom files, so the file name is the only thing left.
        assert offered["local:hall.yaml"] == "acme/light-T1  [custom file: hall.yaml]"
        assert offered["local:porch.yaml"] == (
            "acme/light-T1  [custom file: porch.yaml]"
        )

    async def test_two_products_that_read_the_same_are_told_apart(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """Different products with one label and model get their file named."""
        for product in ("light_a", "light_b"):
            _write_profile(
                own_config_dir,
                f"{product}.yaml",
                _profile(vendor="acme", product=product, label="Light"),
            )

        result = await _configure(hass, await _at_vendor(hass), {FIELD_VENDOR: "acme"})

        offered = _options(result)
        assert offered["local:light_a.yaml"] == "Light-T1  [light_a.yaml]"
        assert offered["local:light_b.yaml"] == "Light-T1  [light_b.yaml]"

    @pytest.mark.usefixtures("repository_source_offered")
    async def test_the_repository_is_named_by_the_pull_request_it_follows(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Its author looks for the pull request's lines, so they say so."""
        products, _ = scan_builtin(SOURCE_BUILTIN)
        switch = next(p for p in products if p.id == "espressif/switch")
        assert switch.content is not None
        serve_ref(aioclient_mock, {**switch.content, "product_label": "Switch, fixed"})
        result = await _configure(
            hass, await _open(hass), {FIELD_REPOSITORY_SOURCE: "123"}
        )

        result = await _configure(hass, result, {FIELD_VENDOR: "espressif"})

        offered = _options(result)
        assert offered[SWITCH_KEY] == "Switch-*  [built-in]"
        assert "Switch, fixed-*  [pull request #123]" in offered.values()

    async def test_a_product_not_in_the_list_is_refused(
        self, hass: HomeAssistant
    ) -> None:
        """The list can change under an open form; the form says so."""
        result = await _pick(hass, await _at_vendor(hass), "espressif", "acme/nothing")

        assert result["step_id"] == STEP_PROFILE_SELECT
        assert result["errors"] == {"base": ERROR_PROFILE_UNKNOWN}

    async def test_a_repository_product_that_cannot_be_fetched_is_refused(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A device is never bound to content nobody has seen."""
        listed = ProductCandidate(
            vendor="acme",
            product="bulb",
            label="Acme Bulb",
            source=SOURCE_REPO,
            hash="0123456789abcdef",
            location="vendors/acme/bulb.yaml",
            url="https://example.invalid/vendors/acme/bulb.yaml",
        )

        async def _online(hass: HomeAssistant) -> tuple[list, str]:
            return [listed], ""

        async def _refuse(
            hass: HomeAssistant, candidate: ProductCandidate, timeout: Any = None
        ) -> ProductCandidate:
            raise CatalogError("HTTP 404")

        monkeypatch.setattr(mirror_module, "async_fetch_index", _online)
        # The listing is offered on the index's word; picking it tries the
        # file, which fails.
        monkeypatch.setattr(mirror_module, "async_download_candidate", _refuse)
        result = await _at_vendor(hass)
        assert "acme" in _options(result)
        result = await _configure(hass, result, {FIELD_VENDOR: "acme"})
        assert _options(result)[listed.key] == "Acme Bulb"

        result = await _configure(hass, result, {FIELD_PROFILE: listed.key})

        assert result["step_id"] == STEP_PROFILE_SELECT
        assert result["errors"] == {"base": ERROR_PROFILE_FETCH_FAILED}


class TestGoingBack:
    """Every form with a step before it offers the way back; a wrong pick is not final.

    Home Assistant's dialogs have no back button, so the forms end with the
    same continue-or-back choice the summary has, set to continue. The list
    itself holds only what it is a list of.
    """

    async def test_the_import_step_has_nothing_to_go_back_to(
        self, hass: HomeAssistant
    ) -> None:
        """The first form offers no back."""
        assert not _offers_back(await _open(hass))

    async def test_the_vendor_list_goes_back_to_the_import_step(
        self, hass: HomeAssistant
    ) -> None:
        """The import step is the one before the vendors; nothing need be picked."""
        result = await _at_vendor(hass)
        assert _offers_back(result)

        result = await _configure(hass, result, {FIELD_ACTION: NAV_ACTION_BACK})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_USER

    async def test_the_product_list_goes_back_to_the_vendors(
        self, hass: HomeAssistant
    ) -> None:
        """Picked the wrong vendor: back to the shelf."""
        result = await _configure(
            hass, await _at_vendor(hass), {FIELD_VENDOR: "espressif"}
        )
        assert result["step_id"] == STEP_PROFILE_SELECT
        assert _offers_back(result)

        result = await _configure(hass, result, {FIELD_ACTION: NAV_ACTION_BACK})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROFILE_VENDOR

    async def test_the_device_list_goes_back_to_the_products_and_scans_again(
        self, hass: HomeAssistant, one_device: None, discovered: AsyncMock
    ) -> None:
        """Picked the wrong product: the next pick looks at the network afresh."""
        result = await _at_device_list(hass)
        assert _offers_back(result)

        result = await _configure(hass, result, {FIELD_ACTION: NAV_ACTION_BACK})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROFILE_SELECT
        result = await _configure(hass, result, {FIELD_PROFILE: SWITCH_KEY})
        assert result["step_id"] == STEP_DEVICE_SETUP
        assert discovered.await_count == 2

    async def test_the_pop_form_goes_back_to_the_device_list(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> None:
        """Picked the wrong device: back to the list, with nothing tried."""
        discovered.return_value = [_advertised()]
        security.ask_device_security.return_value = 1
        result = await _select(hass, await _at_device_list(hass))
        assert result["step_id"] == STEP_POP_INPUT
        assert _offers_back(result)

        result = await _configure(hass, result, {FIELD_ACTION: NAV_ACTION_BACK})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_DEVICE_SETUP
        security.test_pop_connection.assert_not_awaited()

    @pytest.mark.parametrize(
        ("step_id", "error"),
        [
            (STEP_PROFILE_VENDOR, ERROR_VENDOR_UNKNOWN),
            (STEP_PROFILE_SELECT, ERROR_PROFILE_UNKNOWN),
            (STEP_DEVICE_SETUP, ERROR_INVALID_DEVICE),
        ],
        ids=["vendor", "product", "device"],
    )
    async def test_going_on_with_nothing_picked_is_refused(
        self, hass: HomeAssistant, one_device: None, step_id: str, error: str
    ) -> None:
        """The pick is optional only so that going back needs none."""
        result = await _at_vendor(hass)
        if step_id != STEP_PROFILE_VENDOR:
            result = await _configure(hass, result, {FIELD_VENDOR: "espressif"})
        if step_id == STEP_DEVICE_SETUP:
            result = await _configure(hass, result, {FIELD_PROFILE: SWITCH_KEY})
        assert result["step_id"] == step_id

        result = await _configure(hass, result, {FIELD_ACTION: NAV_ACTION_NEXT})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == step_id
        assert result["errors"] == {"base": error}


class TestTheConnectionChoice:
    """Local or cloud, asked only while the cloud is offered.

    Shipped with local control alone, so the question is skipped and the
    product step leads to the network. The step and its text stay for when the
    cloud is turned back on.
    """

    async def test_it_is_not_asked_while_only_local_control_is_offered(
        self, hass: HomeAssistant, discovered: AsyncMock
    ) -> None:
        """One choice is no choice; the user is not made to click through it."""
        result = await _at_local(hass)

        # Straight to the network, and out again: no form in between.
        discovered.assert_awaited_once()
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES

    @pytest.mark.usefixtures("cloud_offered")
    async def test_it_is_asked_once_the_cloud_is_offered(
        self, hass: HomeAssistant, discovered: AsyncMock
    ) -> None:
        """With two ways to reach the device, the product step leads here."""
        result = await _at_connection(hass)

        discovered.assert_not_awaited()
        assert set(result["data_schema"].schema) == {CONF_CONNECTION_MODE}

    @pytest.mark.usefixtures("cloud_offered")
    async def test_choosing_the_network_reaches_the_device(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> None:
        """The local answer leads where the flow goes on its own otherwise."""
        discovered.return_value = [_advertised()]

        result = await _go_local(hass, await _at_connection(hass))

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_DEVICE_SETUP


class TestLocalDiscovery:
    """Finding the device on the network, and listing only the product's."""

    async def test_nothing_on_the_network_ends_the_flow(
        self, hass: HomeAssistant, discovered: AsyncMock
    ) -> None:
        """An empty network is a different problem from the wrong product."""
        result = await _at_local(hass)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES

    async def test_devices_already_added_are_not_offered_again(
        self, hass: HomeAssistant, discovered: AsyncMock
    ) -> None:
        """Every device found is already an entry: nothing left to add."""
        _already_added(hass, TEST_NODE_ID)
        _already_added(hass, "test_node_456")
        discovered.return_value = [
            _advertised(),
            _advertised("test_node_456", "192.168.1.101"),
        ]

        result = await _at_local(hass)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_NEW_DEVICES

    async def test_devices_found_are_listed(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> None:
        """The plain case: two devices on the network, both new, both listed."""
        discovered.return_value = [
            _advertised(),
            _advertised("test_node_456", "192.168.1.101"),
        ]

        result = await _at_device_list(hass)

        assert list(_options(result)) == [TEST_NODE_ID, "test_node_456"]
        placeholders = result["description_placeholders"]
        assert placeholders["matched"].endswith(": 2")
        # Nothing advertised nothing, so no Other device line is shown.
        assert placeholders["silent_note"] == ""

    async def test_only_the_devices_the_product_is_for_are_listed(
        self,
        hass: HomeAssistant,
        own_config_dir: Path,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """Another model: not listed. No record: listed last.

        The product is picked before any device is reached, so the list is
        narrowed to what the model in its mDNS record says fits, and the user
        is not left to work out which of everything on the network their
        choice was for. The model is the only thing judged here: what the
        device is made of is settled entity by entity once it is read. A
        device whose record says nothing (older firmware) cannot be ruled out,
        so it is listed after the rest, as Other device.
        """
        _write_profile(own_config_dir, "bulb.yaml", _profile(model="A1"))
        discovered.return_value = [
            _advertised("a1_one", "10.0.0.1", name=None, model="A1"),
            _advertised("a2", "10.0.0.2", name=None, model="A2"),
            _advertised("old", "10.0.0.4", name=None, model=None),
            _advertised("a1_two", "10.0.0.5", name=None, model="A1"),
        ]

        result = await _at_device_list(hass, "acme", "local:bulb.yaml")

        assert _options(result) == {
            "a1_one": "Bulb-A1-1",
            "a1_two": "Bulb-A1-2",
            "old": "Other device-1",
        }
        placeholders = result["description_placeholders"]
        assert placeholders["matched"] == "Devices matching **acme › Bulb-A1**: 2"
        assert placeholders["silent_note"].startswith("\n\nFound 1")
        assert "Other device" in placeholders["silent_note"]

    async def test_a_star_product_is_for_devices_reporting_the_star_model(
        self,
        hass: HomeAssistant,
        own_config_dir: Path,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """``*`` is a model like any other: a vendor's A1 is not listed under it.

        A device built on the reference profile reports ``*``; one that names
        its own model belongs under its vendor's product, not Espressif's.
        """
        _write_profile(own_config_dir, "bulb.yaml", _profile(model=MODEL_GENERIC))
        discovered.return_value = [
            _advertised("a1", "10.0.0.1", model="A1"),
            _advertised("star", "10.0.0.2", model=MODEL_GENERIC),
        ]

        result = await _at_device_list(hass, "acme", "local:bulb.yaml")

        assert _options(result) == {"star": "Bulb-*-1"}

    async def test_no_fitting_device_ends_the_flow_and_says_what_was_found(
        self, hass: HomeAssistant, own_config_dir: Path, discovered: AsyncMock
    ) -> None:
        """Devices were there; none was this product's. Not an empty list."""
        _write_profile(own_config_dir, "bulb.yaml", _profile(model="A1"))
        discovered.return_value = [
            _advertised("a2", "10.0.0.2", model="A2"),
            _advertised("a3", "10.0.0.3", model="A3"),
        ]

        result = await _at_local(hass, "acme", "local:bulb.yaml")

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_FITTING_DEVICES
        assert result["description_placeholders"] == {
            "product": "acme › Bulb-A1",
            "found": "2",
        }

    async def test_a_device_that_advertised_nothing_is_listed_on_its_own(
        self,
        hass: HomeAssistant,
        own_config_dir: Path,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """Older firmware alone on the network is a list, not an abort.

        It is Other device, not a node id the user cannot read, and
        whether it is the product is settled once it is read.
        """
        _write_profile(own_config_dir, "bulb.yaml", _profile(model="A1"))
        discovered.return_value = [_advertised("old", model=None)]

        result = await _at_device_list(hass, "acme", "local:bulb.yaml")

        assert _options(result) == {"old": "Other device-1"}
        placeholders = result["description_placeholders"]
        assert placeholders["matched"].endswith(": 0")
        assert placeholders["silent_note"].startswith("\n\nFound 1")
        assert "Other device" in placeholders["silent_note"]

    async def test_the_device_list_names_devices_after_the_product(
        self,
        hass: HomeAssistant,
        own_config_dir: Path,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """`Bulb-T1-1`: label, model, number; neither node id nor address."""
        _write_profile(own_config_dir, "bulb.yaml", _profile())
        discovered.return_value = [
            _advertised(name=None, model="T1"),
            _advertised("other", "10.0.0.2", name="Lamp", model="T1"),
        ]
        security.ask_device_security.side_effect = [
            1,
            0,
        ]

        result = await _at_device_list(hass, "acme", "local:bulb.yaml")

        assert _options(result) == {
            TEST_NODE_ID: "Bulb-T1-1",
            "other": "Bulb-T1-2",
        }


class TestDeviceSetup:
    """Picking a device, and what its security level decides."""

    async def test_the_list_names_each_device_by_the_model_it_advertised(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> None:
        """Label, model, number - the model the device said, which the product names."""
        discovered.return_value = [_advertised()]

        result = await _at_device_list(hass)

        assert _device_labels(result) == ["Switch-*-1"]

    async def test_a_value_not_in_the_list_never_reaches_the_flow(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> None:
        """The dropdown is closed: Home Assistant refuses anything else first."""
        discovered.return_value = [_advertised()]
        result = await _at_device_list(hass)

        with pytest.raises(vol.Invalid):
            await _select(hass, result, "invalid_node_id")

    async def test_a_device_without_security_is_read_straight_away(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
    ) -> None:
        """No PoP to ask for, so the entry is created once the node is read."""
        discovered.return_value = [_advertised()]
        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Switch"
        assert result["data"] == {
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_NODE_ID: TEST_NODE_ID,
            CONF_SECURITY_VERSION: 0,
            CONF_CONNECTION_MODE: CONNECTION_MODE_LOCAL,
            CONF_IDENTITY_ADVERTISED: True,
        }
        assert result["result"].unique_id == TEST_NODE_ID

    @pytest.mark.parametrize("version", [1, 2])
    async def test_a_secured_device_is_asked_for_its_pop(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        version: int,
    ) -> None:
        """sec1 and sec2 both need a proof of possession before anything else."""
        discovered.return_value = [_advertised()]
        security.ask_device_security.return_value = version

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_POP_INPUT
        # Called by its line in the list, the one name the user has seen.
        assert result["description_placeholders"] == {"device_name": "Switch-*-1"}

    async def test_a_silent_secured_device_is_asked_for_its_pop_by_its_line(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> None:
        """Older firmware is ``Other device-1`` here too, never its node id."""
        discovered.return_value = [
            _advertised("new", "10.0.0.1"),
            _advertised("6055F9F91114", "10.0.0.2", model=None),
        ]
        security.ask_device_security.return_value = 1

        result = await _select(
            hass, await _at_device_list(hass), node_id="6055F9F91114"
        )

        assert result["step_id"] == STEP_POP_INPUT
        assert result["description_placeholders"] == {"device_name": "Other device-1"}

    @pytest.mark.parametrize(
        "failure",
        [TimeoutError("timeout"), OSError("network")],
        ids=["timeout", "network"],
    )
    async def test_a_device_that_did_not_answer_in_the_scan_is_asked_again(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
        failure: Exception,
    ) -> None:
        """Listed anyway, and asked once more when picked.

        Its HTTP server was still starting when the list was built; by the
        time a person has read the list and picked it, it answers - here as
        sec0, so no PoP is asked for and the entry records what it said.
        """
        discovered.return_value = [_advertised()]
        security.ask_device_security.side_effect = [
            failure,
            0,
        ]

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_SECURITY_VERSION] == 0
        assert security.ask_device_security.await_count == 2

    async def test_a_device_that_never_answers_is_not_added(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """Undetected twice over, the flow ends rather than guessing either way."""
        discovered.return_value = [_advertised()]
        security.ask_device_security.side_effect = [
            TimeoutError("scan"),
            TimeoutError("retry"),
        ]

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_SECURITY_UNKNOWN
        # Called by its line in the list, the one name the user has seen.
        assert result["description_placeholders"] == {"device_name": "Switch-*-1"}
        security.test_pop_connection.assert_not_awaited()

    async def test_a_device_reached_but_not_read_is_no_answer_either(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """The manager's sec1 fallback is not taken for the device's word.

        Told the device requires a code, the user would type one the device
        may never check. The flow only acts on what the device itself said.
        """
        discovered.return_value = [_advertised()]
        security.ask_device_security.return_value = None

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_SECURITY_UNKNOWN

    async def test_a_detection_that_hangs_is_cut_short(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The overall timeout is a backstop for a hang neither side reports.

        The detector never answers and its own timeout is far off; the flow's
        total budget cuts the wait, the device is listed with its security
        unknown, and the question is put to it again once it is picked.
        """
        monkeypatch.setattr(local_module, "SECURITY_DETECT_TIMEOUT", 60)
        monkeypatch.setattr(local_module, "SECURITY_DETECT_TOTAL_TIMEOUT", 0.01)
        calls = 0

        async def hang_once(*_: Any) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(3600)
            return 2

        security.ask_device_security.side_effect = hang_once
        discovered.return_value = [_advertised()]

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_POP_INPUT
        assert calls == 2

    async def test_a_detection_that_is_cancelled_cancels_the_flow(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
    ) -> None:
        """Cancellation is passed on, not written down as an answer."""
        discovered.return_value = [_advertised()]
        security.ask_device_security.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _at_device_list(hass)


class TestPopInput:
    """The proof of possession a sec1 or sec2 device needs."""

    @pytest.fixture
    async def pop_form(
        self, hass: HomeAssistant, discovered: AsyncMock, security: MagicMock
    ) -> Any:
        """Reach the PoP form for one sec1 device."""
        discovered.return_value = [_advertised()]
        security.ask_device_security.return_value = 1
        result = await _select(hass, await _at_device_list(hass))
        assert result["step_id"] == STEP_POP_INPUT
        return result

    async def test_an_empty_pop_is_asked_for_again(
        self, hass: HomeAssistant, pop_form: Any
    ) -> None:
        """Nothing was typed, so nothing is tried."""
        result = await _configure(hass, pop_form, {CONF_CUSTOM_POP: ""})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_POP_INPUT
        assert result["errors"] == {"base": ERROR_POP_REQUIRED}

    async def test_a_pop_the_device_rejects_is_said_so(
        self, hass: HomeAssistant, pop_form: Any, security: MagicMock
    ) -> None:
        """The device said no: a wrong code, to be corrected on the form."""
        security.test_pop_connection.return_value = False

        result = await _configure(hass, pop_form, {CONF_CUSTOM_POP: "wrong_pop"})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_INVALID_POP}

    @pytest.mark.parametrize(
        "failure",
        [
            ConnectionError("Connection refused"),
            TimeoutError("Timed out"),
            OSError("Network unreachable"),
            RuntimeError("Connection Failure"),
            ValueError("bad public key"),
            DecodeError("truncated frame"),
            InvalidTag(),
        ],
        ids=[
            "refused",
            "timeout",
            "unreachable",
            "transport",
            "key",
            "frame",
            "tag",
        ],
    )
    async def test_a_device_that_cannot_be_reached_is_not_blamed_on_the_pop(
        self,
        hass: HomeAssistant,
        pop_form: Any,
        security: MagicMock,
        failure: Exception,
    ) -> None:
        """A dropped connection says nothing about the code that was typed."""
        security.test_pop_connection.side_effect = failure

        result = await _configure(hass, pop_form, {CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_CANNOT_CONNECT}

    async def test_a_pop_the_device_accepts_goes_into_the_entry(
        self, hass: HomeAssistant, pop_form: Any, security: MagicMock, node: MagicMock
    ) -> None:
        """The code is what every later connection will need."""
        result = await _configure(hass, pop_form, {CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_CUSTOM_POP] == TEST_POP
        assert result["data"][CONF_SECURITY_VERSION] == 1
        security.test_pop_connection.assert_awaited_once_with(
            TEST_HOST, TEST_POP, TEST_PORT
        )


class TestReadingTheNode:
    """What the device says it is made of is what the summary is built from."""

    async def test_a_device_that_will_not_answer_is_not_added(
        self, hass: HomeAssistant, one_device: None, node: MagicMock
    ) -> None:
        """Nothing to check a product against means nothing to add.

        Binding it to anything unconfirmed would show a device that looks set
        up and is not. The flow ends with the reason; the user adds it again
        once it answers.
        """
        node.connect.return_value = False

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NODE_UNREAD
        assert not hass.config_entries.async_entries(DOMAIN)
        node.disconnect.assert_awaited_once()

    async def test_a_device_that_drops_mid_read_is_not_added(
        self, hass: HomeAssistant, one_device: None, node: MagicMock
    ) -> None:
        """Answering the handshake is no promise of answering the read.

        The client answers None for a read that went unanswered, and the flow
        treats that as a node that never said what it is made of.
        """
        node.get_property_values.return_value = None

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NODE_UNREAD
        node.disconnect.assert_awaited_once()

    async def test_a_device_that_answers_is_rehearsed_against_the_product(
        self, hass: HomeAssistant, one_device: None, node: MagicMock
    ) -> None:
        """The object model the device sends is what the report is built from.

        The service is one the switch product does not declare: a service is
        not a device type, so it does not make the node another product's,
        and shows up as skipped rather than as a mismatch.
        """
        node.get_property_values.return_value = _properties(
            {
                **SWITCH_CONFIG,
                "services": [
                    {"name": "Diffuser", "type": "esp.service.aroma", "params": []}
                ],
            },
            SWITCH_PARAMS,
        )

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        node.disconnect.assert_awaited_once()


class TestSetupSummary:
    """The product is bound as soon as the node is read.

    What it would build, and which entities it cannot, is written to the log:
    the form that used to spell it out is gone. A skipped entity is logged at
    INFO, an undeclared type at WARNING, and a parameter conflict at ERROR.
    """

    @staticmethod
    def _reports(node: MagicMock, config: dict[str, Any]) -> None:
        """Have the device answer the read with this object model."""
        node.get_property_values.return_value = _properties(config)

    async def test_a_device_the_product_covered_is_added(
        self,
        hass: HomeAssistant,
        one_device: None,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The happy path creates the entry; the log names the entities."""
        caplog.set_level(logging.DEBUG, logger="custom_components.esp_weaver")
        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "creates 1 entities" in caplog.text

    async def test_a_parameter_the_device_never_reported_is_logged(
        self,
        hass: HomeAssistant,
        one_device: None,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Naming the parameter is the whole point: matching is exact."""
        caplog.set_level(logging.INFO, logger="custom_components.esp_weaver")
        self._reports(
            node,
            {
                "info": {"model": MODEL_GENERIC},
                "devices": [
                    {"name": "Switch", "type": "esp.device.switch", "params": []},
                ],
            },
        )

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "does not report" in caplog.text
        assert "`Power`" in caplog.text or "Power" in caplog.text
        assert "left out 1 entities" in caplog.text

    async def test_a_parameter_the_device_describes_otherwise_is_logged(
        self,
        hass: HomeAssistant,
        one_device: None,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A field the profile states and the device contradicts is spelled out."""
        caplog.set_level(logging.ERROR, logger="custom_components.esp_weaver")
        self._reports(
            node,
            {
                "info": {"model": MODEL_GENERIC},
                "devices": [
                    {
                        "name": "Switch",
                        "type": "esp.device.switch",
                        "params": [
                            {
                                "name": "Power",
                                "type": "esp.param.power",
                                "data_type": "bool",
                                "properties": ["read"],
                            }
                        ],
                    },
                ],
            },
        )

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "disagree about its parameters" in caplog.text

    async def test_a_type_the_product_did_not_claim_is_logged(
        self,
        hass: HomeAssistant,
        one_device: None,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """That device produces no entities at all, which is worth knowing."""
        caplog.set_level(logging.INFO, logger="custom_components.esp_weaver")
        self._reports(
            node,
            {
                **SWITCH_CONFIG,
                "services": [
                    {"name": "Diffuser", "type": "esp.service.aroma", "params": []}
                ],
            },
        )

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "esp.service.aroma" in caplog.text
        assert "types with no profile" in caplog.text

    async def test_a_node_with_both_troubles_is_still_added(
        self,
        hass: HomeAssistant,
        one_device: None,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Neither half may hide the other; the device is bound either way."""
        caplog.set_level(logging.INFO, logger="custom_components.esp_weaver")
        self._reports(
            node,
            {
                "info": {"model": MODEL_GENERIC},
                "devices": [
                    {"name": "Switch", "type": "esp.device.switch", "params": []},
                ],
                "services": [
                    {"name": "Diffuser", "type": "esp.service.aroma", "params": []}
                ],
            },
        )

        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "left out" in caplog.text
        assert "esp.service.aroma" in caplog.text

    async def test_confirming_creates_the_entry_bound_to_the_product(
        self, hass: HomeAssistant, one_device: None, node: MagicMock
    ) -> None:
        """What it creates is bound to the product that was rehearsed."""
        result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        binding = ProfileBinding.from_entry(result["result"])
        assert binding is not None
        assert binding.id == SWITCH_PRODUCT
        store = await async_get_store(hass)
        assert store.get(binding.id, binding.hash) is not None

    async def test_a_type_the_product_does_not_declare_is_unknown_not_a_mismatch(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A node of two types, under a product declaring one: added, not refused.

        Device types are not part of the identity; the model is. The type the
        product does not declare is logged as unknown.
        """
        caplog.set_level(logging.INFO, logger="custom_components.esp_weaver")
        discovered.return_value = [_advertised()]
        self._reports(
            node,
            {
                "devices": [
                    {
                        "name": "Light",
                        "type": "esp.device.lightbulb",
                        "params": [{"name": "Power"}],
                    },
                    {
                        "name": "Sensor",
                        "type": "esp.device.temperature-sensor",
                        "params": [{"name": "Temperature"}],
                    },
                ],
                "info": {"model": MODEL_GENERIC},
            },
        )
        result = await _select(hass, await _at_device_list(hass, key=LIGHTBULB_KEY))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "esp.device.temperature-sensor" in caplog.text

    async def test_a_device_that_advertised_nothing_is_not_judged_on_its_model(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
    ) -> None:
        """The model is not held against older firmware, which reports its project name.

        The entry remembers the leniency for a rebind.
        """
        discovered.return_value = [_advertised("old", model=None)]
        self._reports(node, {**SWITCH_CONFIG, "info": {"model": "led_light"}})

        result = await _select(hass, await _at_device_list(hass), "old")

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_IDENTITY_ADVERTISED] is False

    async def test_a_device_that_advertised_nothing_is_added_not_refused(
        self,
        hass: HomeAssistant,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Nothing to judge it by: it is added; the log says what the product missed.

        A lightbulb under the switch product gets no entity; the type is logged.
        """
        caplog.set_level(logging.INFO, logger="custom_components.esp_weaver")
        discovered.return_value = [_advertised("old", model=None)]
        self._reports(
            node,
            {
                "devices": [{"name": "Light", "type": "esp.device.lightbulb"}],
                "info": {"model": "led_light"},
            },
        )
        result = await _at_device_list(hass, "espressif", SWITCH_KEY)

        result = await _select(hass, result, "old")

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert "esp.device.lightbulb" in caplog.text

    async def test_a_mismatch_read_again_is_shown_again(
        self,
        hass: HomeAssistant,
        own_config_dir: Path,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
    ) -> None:
        """The same fetch on the mismatch form: nothing is chosen for the user."""
        _write_profile(own_config_dir, "bulb.yaml", _profile(model="A1"))
        discovered.return_value = [_advertised(model="A1")]
        result = await _select(
            hass, await _at_device_list(hass, "acme", "local:bulb.yaml")
        )
        assert result["step_id"] == STEP_DEVICE_MISMATCH

        again = await hass.config_entries.flow.async_configure(result["flow_id"])

        assert again["type"] is FlowResultType.FORM
        assert again["step_id"] == STEP_DEVICE_MISMATCH
        assert again["description_placeholders"] == result["description_placeholders"]

    async def test_a_mismatch_can_be_replaced_without_reconnecting(
        self,
        hass: HomeAssistant,
        own_config_dir: Path,
        discovered: AsyncMock,
        security: MagicMock,
        node: MagicMock,
    ) -> None:
        """A fitting second product uses the device that is already connected."""
        _write_profile(own_config_dir, "bulb.yaml", _profile(model="A1"))
        discovered.return_value = [_advertised(model="A1")]
        result = await _select(
            hass, await _at_device_list(hass, "acme", "local:bulb.yaml")
        )
        assert result["step_id"] == STEP_DEVICE_MISMATCH

        result = await _configure(hass, result, {})
        assert result["step_id"] == STEP_PROFILE_VENDOR
        result = await _pick(hass, result, "espressif", SWITCH_KEY)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        discovered.assert_awaited_once()

    async def test_a_rehearsal_that_raises_ends_the_flow_without_an_entry(
        self, hass: HomeAssistant, one_device: None, node: MagicMock
    ) -> None:
        """A choice nobody has seen the result of is not bound.

        The bug is logged with a traceback; the flow ends saying the summary
        failed rather than quietly creating an entry behind it.
        """
        with patch(
            "custom_components.esp_weaver.flows.profile_choice.build_preview",
            side_effect=ValueError("malformed profile"),
        ):
            result = await _select(hass, await _at_device_list(hass))

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_SUMMARY_FAILED
        assert not hass.config_entries.async_entries(DOMAIN)


class TestServerRenderedMessages:
    """The fragments the flow renders itself: English must have every key."""

    EXPECTED: ClassVar[dict[str, set[str]]] = {
        MESSAGE_PROFILE_IMPORTED: {"filename"},
        MESSAGE_DEVICE_ONLINE: set(),
        MESSAGE_DEVICE_OFFLINE: set(),
        MESSAGE_REPOSITORY_OFFLINE: set(),
        **{f"{MESSAGE_SOURCE_PREFIX}{source}": set() for source in SOURCE_ORDER},
        **{f"{MESSAGE_MISMATCH_PREFIX}{reason}": set() for reason in (MISFIT_MODEL,)},
    }

    @pytest.mark.parametrize(
        "catalogue",
        ["strings.json", "translations/en.json", "translations/zh-Hans.json"],
    )
    def test_every_message_is_in_every_language(self, catalogue: str) -> None:
        """Each key the code asks for, with exactly the fields it fills in."""
        root = Path(__file__).parents[2] / "custom_components" / DOMAIN
        text = (root / catalogue).read_text(encoding="utf-8")
        common = json.loads(text)[COMMON_CATEGORY]

        for key, placeholders in self.EXPECTED.items():
            assert key in common, f"{catalogue} has no common.{key}"
            named = {
                field
                for _, field, _, _ in string.Formatter().parse(common[key])
                if field
            }
            assert named == placeholders, f"{catalogue} common.{key}"

    @pytest.mark.usefixtures("cloud_offered")
    async def test_the_message_follows_the_servers_language(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The label is built here, so it is the server's language it follows."""
        await hass.config.async_update(language="zh-Hans")
        root = Path(__file__).parents[2] / "custom_components" / DOMAIN
        text = (root / "translations" / "zh-Hans.json").read_text(encoding="utf-8")
        online = json.loads(text)[COMMON_CATEGORY][MESSAGE_DEVICE_ONLINE]
        assert online != "Online"
        cli.get_rainmaker_devices.return_value = [CLOUD_DEVICE]

        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )

        assert result["step_id"] == STEP_CLOUD_DEVICE_SELECT
        assert _options(result) == {"ABC123": f"Test Device (ABC123) - {online}"}


class TestDeviceNaming:
    """A device is named what its node calls it.

    The name is the one thing about a device that shows everywhere, so it is
    the one the firmware reports - the `Name` parameter, which the user may
    have set, or the name the device is declared under. What was advertised
    over mDNS before the node was read is the fallback, and the id the last.
    """

    BULB_CONFIG: dict[str, Any] = {
        "devices": [
            {
                "name": "Light",
                "type": "esp.device.lightbulb",
                "params": [{"name": "Power"}],
            }
        ],
        "info": {"model": "T1"},
    }

    @pytest.fixture
    def acme(self, own_config_dir: Path, security: MagicMock, node: MagicMock) -> None:
        """Acme's bulb, and a bulb on the network that is one."""
        _write_profile(own_config_dir, "bulb.yaml", _profile(vendor="acme"))
        node.get_property_values.return_value = _properties(self.BULB_CONFIG)

    @staticmethod
    async def _added(
        hass: HomeAssistant, discovered: AsyncMock, name: str | None
    ) -> Any:
        """Add the one bulb on the network as Acme's, and return the result."""
        discovered.return_value = [_advertised(name=name, model="T1")]
        result = await _select(
            hass, await _at_device_list(hass, "acme", "local:bulb.yaml")
        )
        return await _confirm(hass, result)

    async def test_a_device_is_called_what_its_one_device_is(
        self, hass: HomeAssistant, acme: None, discovered: AsyncMock
    ) -> None:
        """`Light`, not `acme-<node id>`: what the firmware calls it."""
        result = await self._added(hass, discovered, None)

        assert result["title"] == "Light"

    async def test_the_name_parameter_is_what_the_device_is_called(
        self, hass: HomeAssistant, acme: None, node: MagicMock, discovered: AsyncMock
    ) -> None:
        """The user renamed it in the app; the node config still says `Light`."""
        config = {
            "devices": [
                {
                    "name": "Light",
                    "type": "esp.device.lightbulb",
                    "params": [
                        {"name": "Name", "type": "esp.param.name"},
                        {"name": "Power"},
                    ],
                }
            ],
            "info": {"model": "T1"},
        }
        node.get_property_values.return_value = _properties(
            config, {"Light": {"Name": "Bedroom light", "Power": True}}
        )

        result = await self._added(hass, discovered, None)

        assert result["title"] == "Bedroom light"

    async def test_an_empty_name_parameter_leaves_the_declared_name(
        self, hass: HomeAssistant, acme: None, node: MagicMock, discovered: AsyncMock
    ) -> None:
        """A `Name` of blanks names nothing; the declared name stands."""
        config = {
            "devices": [
                {
                    "name": "Light",
                    "type": "esp.device.lightbulb",
                    "params": [{"name": "Name", "type": "esp.param.name"}],
                }
            ],
            "info": {"model": "T1"},
        }
        node.get_property_values.return_value = _properties(
            config, {"Light": {"Name": "  "}}
        )

        result = await self._added(hass, discovered, None)

        assert result["title"] == "Light"

    async def test_a_node_of_several_devices_goes_by_its_own_name(
        self, hass: HomeAssistant, acme: None, node: MagicMock, discovered: AsyncMock
    ) -> None:
        """Two lights, two names; the node's own name covers both."""
        node.get_property_values.return_value = _properties(
            {
                "devices": [
                    {"name": "Light", "type": "esp.device.lightbulb", "params": []},
                    {"name": "Lamp", "type": "esp.device.lightbulb", "params": []},
                ],
                "info": {"name": "Bedroom", "model": "T1"},
            }
        )

        result = await self._added(hass, discovered, None)

        assert result["title"] == "Bedroom"

    async def test_a_node_of_several_devices_with_no_name_takes_the_first(
        self, hass: HomeAssistant, acme: None, node: MagicMock, discovered: AsyncMock
    ) -> None:
        """No node name to fall back on: the first device's, not the node id."""
        node.get_property_values.return_value = _properties(
            {
                "devices": [
                    {"name": "Light", "type": "esp.device.lightbulb", "params": []},
                    {"name": "Lamp", "type": "esp.device.lightbulb", "params": []},
                ],
                "info": {"model": "T1"},
            }
        )

        result = await self._added(hass, discovered, None)

        assert result["title"] == "Light"

    async def test_a_node_config_that_names_nothing_is_an_other_device(
        self, hass: HomeAssistant, acme: None, node: MagicMock, discovered: AsyncMock
    ) -> None:
        """Nothing to call it by: not a vendor's name, not the node id."""
        node.get_property_values.return_value = _properties(
            {
                "devices": [{"type": "esp.device.lightbulb", "params": []}],
                "info": {"model": "T1"},
            }
        )

        result = await self._added(hass, discovered, None)

        assert result["title"] == "Other device"

    async def test_the_node_outranks_the_name_advertised_over_mdns(
        self, hass: HomeAssistant, acme: None, discovered: AsyncMock
    ) -> None:
        """Both paths name the device the same way: by what the node says."""
        result = await self._added(hass, discovered, TEST_DEVICE_NAME)

        assert result["title"] == "Light"

    async def test_the_advertised_name_serves_when_the_node_names_nothing(
        self, hass: HomeAssistant, acme: None, node: MagicMock, discovered: AsyncMock
    ) -> None:
        """Better than the id: a name the firmware did give, if only over mDNS."""
        node.get_property_values.return_value = _properties(
            {
                "devices": [{"type": "esp.device.lightbulb", "params": []}],
                "info": {"model": "T1"},
            }
        )

        result = await self._added(hass, discovered, TEST_DEVICE_NAME)

        assert result["title"] == TEST_DEVICE_NAME

    async def test_the_name_does_not_follow_the_product(
        self,
        hass: HomeAssistant,
        acme: None,
        own_config_dir: Path,
        discovered: AsyncMock,
    ) -> None:
        """The product is zeta's; the device is still a Light."""
        _write_profile(own_config_dir, "zeta.yaml", _profile(vendor="zeta"))
        discovered.return_value = [_advertised(name=None, model="T1")]
        result = await _select(
            hass, await _at_device_list(hass, "zeta", "local:zeta.yaml")
        )

        assert result["title"] == "Light"

    @pytest.mark.usefixtures("cloud_offered")
    async def test_a_cloud_device_keeps_its_marker(
        self, hass: HomeAssistant, own_config_dir: Path, cli: MagicMock
    ) -> None:
        """The suffix that tells a cloud entry from a local one stays."""
        _write_profile(own_config_dir, "bulb.yaml", _profile(vendor="acme"))
        cli.get_rainmaker_devices.return_value = [
            CloudDeviceInfo(node_id="ABC123", name="", connected=True)
        ]
        cli.get_node_details.return_value = self.BULB_CONFIG
        result = await _pick(hass, await _at_vendor(hass), "acme", "local:bulb.yaml")
        result = await _configure(
            hass, result, {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD}
        )
        result = await _configure(hass, result, LOGIN)
        result = await _configure(hass, result, {FIELD_ACTION: CLOUD_ACTION_EXISTING})
        result = await _select(hass, result, "ABC123")

        result = await _confirm(hass, result)

        assert result["title"] == "Light (Cloud)"

    async def test_the_pop_form_names_the_device_by_its_line_in_the_list(
        self,
        hass: HomeAssistant,
        acme: None,
        security: MagicMock,
        discovered: AsyncMock,
    ) -> None:
        """Asked before the node is read, so the list's line is what it goes by."""
        discovered.return_value = [
            _advertised("other", "10.0.0.2", name=None, model="T1"),
            _advertised(name=None, model="T1"),
        ]
        security.ask_device_security.return_value = 1

        result = await _select(
            hass, await _at_device_list(hass, "acme", "local:bulb.yaml")
        )

        assert result["step_id"] == STEP_POP_INPUT
        assert result["description_placeholders"]["device_name"] == "Bulb-T1-2"


@pytest.mark.usefixtures("cloud_offered")
class TestCloudLogin:
    """Signing in to RainMaker."""

    async def test_choosing_the_cloud_asks_for_the_account(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Nothing can be read from the account before it is signed in to."""
        cli.check_login_status.return_value = {"logged_in": False}

        result = await _configure(
            hass,
            await _at_connection(hass),
            {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_RAINMAKER_LOGIN

    async def test_signing_in_leads_to_the_choice_of_what_to_add(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The plain case."""
        cli.check_login_status.return_value = {"logged_in": False}

        result = await _at_cloud_actions(hass)

        assert result["type"] is FlowResultType.FORM
        cli.login.assert_awaited_once_with(LOGIN[CONF_EMAIL], LOGIN[CONF_PASSWORD])

    async def test_a_refused_login_is_said_on_the_form(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Wrong credentials are corrected on the form, not by starting over."""
        cli.check_login_status.return_value = {"logged_in": False}
        cli.login.return_value = {"success": False, "error": "Invalid credentials"}
        result = await _configure(
            hass,
            await _at_connection(hass),
            {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD},
        )

        result = await _configure(hass, result, LOGIN)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_RAINMAKER_LOGIN
        assert result["errors"] == {"base": ERROR_RAINMAKER_LOGIN_FAILED}

    async def test_an_empty_form_without_a_session_is_refused(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Nothing to sign in with, and nothing signed in already."""
        cli.check_login_status.return_value = {"logged_in": False}
        result = await _configure(
            hass,
            await _at_connection(hass),
            {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD},
        )

        result = await _configure(hass, result, {CONF_EMAIL: "", CONF_PASSWORD: ""})

        assert result["step_id"] == STEP_RAINMAKER_LOGIN
        assert result["errors"] == {"base": ERROR_RAINMAKER_NOT_LOGGED_IN}
        cli.login.assert_not_awaited()

    async def test_a_session_the_tool_still_has_is_used(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The tool keeps its own session; the form is only used when it is gone."""
        result = await _at_cloud_actions(hass)

        assert result["type"] is FlowResultType.FORM
        cli.login.assert_not_awaited()
        cli.logout.assert_not_awaited()

    async def test_a_session_for_the_same_account_is_kept_whatever_the_case(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Typing the address in another case is not asking for another account."""
        cli.check_login_status.return_value = {
            "logged_in": True,
            "email": LOGIN[CONF_EMAIL].lower(),
        }
        result = await _configure(
            hass,
            await _at_connection(hass),
            {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD},
        )

        result = await _configure(
            hass, result, {**LOGIN, CONF_EMAIL: LOGIN[CONF_EMAIL].upper()}
        )

        assert result["step_id"] == STEP_CLOUD_ACTION_SELECT
        cli.logout.assert_not_awaited()
        cli.login.assert_not_awaited()

    async def test_a_session_for_another_account_is_ended_first(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The account typed is the one signed in, not whoever signed in last.

        The tool will not take new credentials while it holds a session, and
        asks on a terminal nobody is at; so the session is ended, then the
        typed account is signed in.
        """
        cli.check_login_status.return_value = {
            "logged_in": True,
            "email": "someone.else@example.com",
        }
        result = await _configure(
            hass,
            await _at_connection(hass),
            {CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD},
        )

        result = await _configure(hass, result, LOGIN)

        assert result["step_id"] == STEP_CLOUD_ACTION_SELECT
        cli.logout.assert_awaited_once()
        cli.login.assert_awaited_once_with(LOGIN[CONF_EMAIL], LOGIN[CONF_PASSWORD])


@pytest.mark.usefixtures("cloud_offered")
class TestCloudDevices:
    """Adding a device the account already has."""

    async def test_the_accounts_devices_are_listed(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Named, with the node id to tell two alike apart, and whether online."""
        cli.get_rainmaker_devices.return_value = [
            CLOUD_DEVICE,
            CloudDeviceInfo(node_id="DEF456", name="Other", connected=False),
        ]

        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_CLOUD_DEVICE_SELECT
        assert _options(result) == {
            "ABC123": "Test Device (ABC123) - Online",
            "DEF456": "Other (DEF456) - Offline",
        }
        assert result["description_placeholders"]["device_count"] == "2"

    async def test_an_empty_account_ends_the_flow(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Asked more than once, since an empty answer is also how the API stumbles."""
        cli.get_rainmaker_devices.return_value = []

        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES
        assert cli.get_rainmaker_devices.await_count == CLOUD_DEVICE_FETCH_ATTEMPTS

    async def test_an_api_that_errors_is_treated_as_an_empty_account(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Retried like an empty answer, and given up on the same way."""
        cli.get_rainmaker_devices.side_effect = RuntimeError("API timeout")

        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES

    async def test_devices_already_added_are_not_offered_again(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The one device in the account is already an entry."""
        _already_added(hass, "ABC123")

        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_NEW_DEVICES

    async def test_a_value_not_in_the_list_never_reaches_the_flow(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The dropdown is closed: Home Assistant refuses anything else first."""
        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )

        with pytest.raises(vol.Invalid):
            await _select(hass, result, "NONEXISTENT_DEVICE")

    async def test_choosing_a_device_creates_a_cloud_entry(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Reached through the account, so the entry says so and holds no address."""
        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )
        result = await _select(hass, result, "ABC123")

        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Switch (Cloud)"
        assert result["data"] == {
            CONF_NODE_ID: "ABC123",
            CONF_CONNECTION_MODE: CONNECTION_MODE_CLOUD,
            "rainmaker_region": "global",
        }
        assert result["result"].unique_id == "ABC123"


@pytest.mark.usefixtures("cloud_offered")
class TestWaitingForTheNodeConfig:
    """An entry is not created before the cloud can describe the node."""

    async def _pick_the_device(self, hass: HomeAssistant) -> Any:
        """Choose the account's one device, which starts the wait."""
        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_EXISTING}
        )
        return await _select(hass, result, "ABC123")

    async def test_a_config_that_arrives_late_is_waited_for(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Asked again until the cloud has something to say."""
        cli.get_node_details.side_effect = [None, {}, CLOUD_NODE_CONFIG]

        result = await self._pick_the_device(hass)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert cli.get_node_details.await_count == 3

    async def test_a_config_that_never_arrives_does_not_hold_the_flow_forever(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The wait runs out, and a node nobody can describe is not added."""
        cli.get_node_details.return_value = None

        result = await self._pick_the_device(hass)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NODE_UNREAD
        assert cli.get_node_details.await_count == CLOUD_CONFIG_WAIT_ATTEMPTS

    async def test_a_cloud_that_errors_is_treated_as_not_ready(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """A failing lookup is retried rather than raised."""
        cli.get_node_details.side_effect = [
            RuntimeError("cli exploded"),
            CLOUD_NODE_CONFIG,
        ]

        result = await self._pick_the_device(hass)

        assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("cloud_offered")
class TestProvisioning:
    """Giving a new device its network and waiting for the account to list it."""

    async def test_choosing_to_provision_asks_for_the_device(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The device is named, not scanned for: see BLE_SCAN_SCHEMA."""
        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_PROVISION}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_BLE_SCAN

    async def test_an_empty_device_name_is_asked_for_again(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Whitespace is not a name."""
        result = await _configure(
            hass, await _at_cloud_actions(hass), {FIELD_ACTION: CLOUD_ACTION_PROVISION}
        )

        result = await _configure(hass, result, {FIELD_DEVICE_NAME: "  "})

        assert result["step_id"] == STEP_BLE_SCAN
        assert result["errors"] == {"base": ERROR_INVALID_DEVICE}

    @pytest.mark.parametrize("device_name", ["PROV_123456", "CUSTOM_DEVICE"])
    async def test_a_named_device_leads_to_the_wifi_form(
        self, hass: HomeAssistant, cli: MagicMock, device_name: str
    ) -> None:
        """A name outside the usual convention is warned about, not refused."""
        result = await _at_wifi(hass, device_name)

        assert result["type"] is FlowResultType.FORM
        assert result["description_placeholders"]["device_name"] == device_name

    async def test_a_network_without_a_name_is_asked_for_again(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The SSID is the one thing the device cannot do without."""
        result = await _configure(
            hass, await _at_wifi(hass), {**WIFI, FIELD_WIFI_SSID: ""}
        )

        assert result["step_id"] == STEP_WIFI_CONFIG
        assert result["errors"] == {"base": ERROR_WIFI_SSID_REQUIRED}
        cli.provision_device.assert_not_awaited()

    async def test_a_network_without_a_password_is_asked_for_again(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The tool cannot join an open network without a terminal to ask on.

        Given an SSID and no passphrase it drops into an interactive picker
        and sits there until its timeout; better to say so on the form.
        """
        result = await _configure(
            hass, await _at_wifi(hass), {**WIFI, FIELD_WIFI_PASSWORD: ""}
        )

        assert result["step_id"] == STEP_WIFI_CONFIG
        assert result["errors"] == {"base": ERROR_WIFI_PASSWORD_REQUIRED}
        cli.provision_device.assert_not_awaited()

    async def test_provisioning_shows_progress_while_it_runs(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Minutes of Bluetooth and cloud are not spent holding a form open.

        The run is a task behind a progress dialog naming the device; when
        it ends, Home Assistant calls the step again and the flow moves on.
        """
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_provision(**_: Any) -> dict[str, Any]:
            started.set()
            await release.wait()
            return {"success": True, "node_id": "NEW123"}

        cli.provision_device.side_effect = slow_provision
        cli.get_rainmaker_devices.return_value = [
            CloudDeviceInfo(node_id="NEW123", name="Provisioned")
        ]

        result = await _configure(hass, await _at_wifi(hass), WIFI)

        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == STEP_PROVISIONING
        assert result["progress_action"] == PROGRESS_PROVISIONING
        assert result["description_placeholders"] == {"device_name": "PROV_123456"}
        await started.wait()

        # Asked again while the run is still going: still progress.
        result = await _configure(hass, result, None)
        assert result["type"] is FlowResultType.SHOW_PROGRESS

        release.set()
        await hass.async_block_till_done()
        result = await _configure(hass, result, None)

        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_a_provisioning_that_fails_offers_retry_or_back(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The tool's own words are shown, and the WiFi details can be corrected."""
        cli.provision_device.return_value = {
            "success": False,
            "error": "BLE connection failed",
        }

        result = await _provision(hass, await _at_wifi(hass))

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROVISIONING_FAILED
        assert result["errors"] == {"base": ERROR_PROVISIONING_FAILED}
        assert result["description_placeholders"]["error"] == "BLE connection failed"
        cli.provision_device.assert_awaited_once_with(
            device_name="PROV_123456",
            ssid="TestWiFi",
            wifi_password="wifi_pass",
            pop="",
        )

    async def test_a_run_that_breaks_is_reported_not_lost(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """A bug in the run reaches the dialog as a failure, with a way on."""
        cli.provision_device.side_effect = RuntimeError("no adapter")

        result = await _provision(hass, await _at_wifi(hass))

        assert result["step_id"] == STEP_PROVISIONING_FAILED
        assert result["errors"] == {"base": ERROR_PROVISIONING_FAILED}
        assert result["description_placeholders"] == {"error": "no adapter"}

    async def test_the_failure_form_is_shown_again_as_it_was(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Reopening the dialog rereads the form; the error is still on it."""
        cli.provision_device.return_value = {"success": False, "error": "auth"}
        result = await _provision(hass, await _at_wifi(hass))

        result = await hass.config_entries.flow.async_configure(result["flow_id"])

        assert result["step_id"] == STEP_PROVISIONING_FAILED
        assert result["errors"] == {"base": ERROR_PROVISIONING_FAILED}
        assert result["description_placeholders"] == {"error": "auth"}

    async def test_going_back_returns_to_the_wifi_form(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """A wrong password is corrected without starting over."""
        cli.provision_device.return_value = {"success": False, "error": "auth"}
        result = await _provision(hass, await _at_wifi(hass))

        result = await _configure(
            hass, result, {FIELD_ACTION: PROVISIONING_ACTION_BACK}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_WIFI_CONFIG

    async def test_retrying_provisions_again(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The same details, tried once more."""
        cli.provision_device.side_effect = [
            {"success": False, "error": "BLE connection failed"},
            {"success": True, "node_id": "NEW123"},
        ]
        cli.get_rainmaker_devices.return_value = [
            CloudDeviceInfo(node_id="NEW123", name="Provisioned")
        ]
        result = await _provision(hass, await _at_wifi(hass))

        result = await _retry(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert cli.provision_device.await_count == 2

    async def test_a_provisioned_device_becomes_a_cloud_entry(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The node the tool reports is the one added, once the account lists it."""
        cli.get_rainmaker_devices.return_value = [
            CLOUD_DEVICE,
            CloudDeviceInfo(
                node_id="NEW123", name="Provisioned Device", connected=True
            ),
        ]

        result = await _provision(hass, await _at_wifi(hass))
        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Switch (Cloud)"
        assert result["data"][CONF_NODE_ID] == "NEW123"
        assert result["data"][CONF_CONNECTION_MODE] == CONNECTION_MODE_CLOUD
        # The node's configuration was read once, during the run.
        cli.get_node_details.assert_awaited_once_with("NEW123")

    async def test_a_device_that_appears_later_is_waited_for(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """The tool answers well before the cloud lists the device.

        The first read is the snapshot taken before the tool runs; the polls
        that follow are what wait for the device.
        """
        cli.get_rainmaker_devices.side_effect = [
            [],
            [],
            [],
            [CloudDeviceInfo(node_id="NEW123", name="New Device", connected=True)],
        ]

        result = await _provision(hass, await _at_wifi(hass))
        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_NODE_ID] == "NEW123"
        assert cli.get_rainmaker_devices.await_count == 4

    async def test_a_device_that_never_appears_is_said_so(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Provisioned, but not in the account: a different problem, named as such."""
        cli.get_rainmaker_devices.return_value = []

        result = await _provision(hass, await _at_wifi(hass))

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROVISIONING_FAILED
        assert result["errors"] == {"base": ERROR_PROVISIONING_NOT_IN_CLOUD}
        # One snapshot, then every poll.
        assert cli.get_rainmaker_devices.await_count == PROVISION_POLL_ATTEMPTS + 1

    async def test_an_account_that_cannot_be_read_while_waiting_is_asked_again(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """One failed poll is not the device's fault; every poll failing is the end."""
        cli.get_rainmaker_devices.side_effect = RuntimeError("Cloud API error")

        result = await _provision(hass, await _at_wifi(hass))

        assert result["step_id"] == STEP_PROVISIONING_FAILED
        assert result["errors"] == {"base": ERROR_PROVISIONING_NOT_IN_CLOUD}
        assert cli.get_rainmaker_devices.await_count == PROVISION_POLL_ATTEMPTS + 1

    async def test_without_a_node_id_the_new_device_is_the_one_the_account_gained(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """An older tool reports no node id; new means not listed before the run.

        The account already held a node that was never added here. It is not
        the new one, and is not mistaken for it.
        """
        cli.provision_device.return_value = {"success": True, "node_id": None}
        cli.get_rainmaker_devices.side_effect = [
            [CLOUD_DEVICE],
            [CLOUD_DEVICE, CloudDeviceInfo(node_id="DEV1", name="Device 1")],
        ]

        result = await _provision(hass, await _at_wifi(hass))
        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_NODE_ID] == "DEV1"

    async def test_without_a_node_id_several_new_devices_are_not_guessed_between(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Two nodes joined the account meanwhile: neither is bound unlooked at.

        The form names them both, so the user can tell which is theirs and
        add it from the account instead.
        """
        _already_added(hass, "ABC123")
        cli.provision_device.return_value = {"success": True, "node_id": None}
        cli.get_rainmaker_devices.side_effect = [
            [CLOUD_DEVICE],
            [
                CLOUD_DEVICE,
                CloudDeviceInfo(node_id="DEV1", name="Device 1"),
                CloudDeviceInfo(node_id="DEV2", name="Device 2"),
            ],
        ]

        result = await _provision(hass, await _at_wifi(hass))

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROVISIONING_FAILED
        assert result["errors"] == {"base": ERROR_PROVISIONING_AMBIGUOUS}
        assert result["description_placeholders"] == {"error": "DEV1, DEV2"}
        assert {e.unique_id for e in hass.config_entries.async_entries(DOMAIN)} == {
            "ABC123"
        }

    async def test_without_a_node_id_a_device_already_known_is_not_the_new_one(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """Until the account lists something it did not before, the wait goes on."""
        _already_added(hass, "ABC123")
        cli.provision_device.return_value = {"success": True, "node_id": None}
        cli.get_rainmaker_devices.side_effect = [
            [CLOUD_DEVICE],
            [CLOUD_DEVICE],
            [CLOUD_DEVICE, CloudDeviceInfo(node_id="DEV1", name="Device 1")],
        ]

        result = await _provision(hass, await _at_wifi(hass))
        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_NODE_ID] == "DEV1"
        assert cli.get_rainmaker_devices.await_count == 3

    async def test_an_account_unreadable_before_the_run_falls_back_to_the_entries(
        self, hass: HomeAssistant, cli: MagicMock
    ) -> None:
        """No snapshot to compare with: whatever is not configured here is new."""
        _already_added(hass, "ABC123")
        cli.provision_device.return_value = {"success": True, "node_id": None}
        cli.get_rainmaker_devices.side_effect = [
            RuntimeError("Cloud API error"),
            [CLOUD_DEVICE, CloudDeviceInfo(node_id="DEV1", name="Device 1")],
        ]

        result = await _provision(hass, await _at_wifi(hass))
        result = await _confirm(hass, result)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_NODE_ID] == "DEV1"


class TestOptionsFlow:
    """Changing which product a device resolves against, after it was added."""

    @pytest.fixture
    def entry(self, hass: HomeAssistant) -> MockConfigEntry:
        """An entry bound to the switch product, with another option besides."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_NODE_ID: TEST_NODE_ID,
                CONF_HOST: TEST_HOST,
                CONF_PORT: TEST_PORT,
            },
            options={
                OPTION_PROFILE: {
                    "id": SWITCH_PRODUCT,
                    "source": "builtin",
                    "hash": "x",
                },
                "available_firmware": "1.0",
            },
            unique_id=TEST_NODE_ID,
        )
        entry.add_to_hass(hass)
        return entry

    @pytest.fixture
    def reported(self, hass: HomeAssistant) -> MagicMock:
        """The node's config as the API has it, which the options flow reads."""
        api = MagicMock()
        api.get_reported_config.return_value = (SWITCH_CONFIG, SWITCH_PARAMS)
        domain_data(hass).api = api
        return api

    async def _at_products(self, hass: HomeAssistant, entry: MockConfigEntry) -> Any:
        """Open the options and pick Espressif, landing on its product list."""
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_INIT
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {FIELD_VENDOR: "espressif"}
        )
        assert result["step_id"] == STEP_PROFILE_SELECT
        return result

    async def _answer(self, hass: HomeAssistant, result: Any, user_input: Any) -> Any:
        """Answer the options form being shown."""
        return await hass.config_entries.options.async_configure(
            result["flow_id"], user_input
        )

    async def test_a_device_that_advertised_nothing_rebinds_on_its_types(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """The leniency the entry was added under holds for a rebind."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_IDENTITY_ADVERTISED: False}
        )
        reported.get_reported_config.return_value = (
            {**SWITCH_CONFIG, "info": {"model": "led_light"}},
            SWITCH_PARAMS,
        )

        result = await self._answer(
            hass, await self._at_products(hass, entry), {FIELD_PROFILE: SWITCH_KEY}
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_a_device_that_advertised_itself_is_held_to_the_model_on_a_rebind(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """An entry without the flag - a cloud device, say - is judged in full."""
        reported.get_reported_config.return_value = (
            {**SWITCH_CONFIG, "info": {"model": "led_light"}},
            SWITCH_PARAMS,
        )

        result = await self._answer(
            hass, await self._at_products(hass, entry), {FIELD_PROFILE: SWITCH_KEY}
        )

        assert result["step_id"] == STEP_DEVICE_MISMATCH

    async def test_the_product_list_goes_back_to_the_vendors(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """The options flow's product list has the same way back as the config flow's."""
        result = await self._at_products(hass, entry)
        assert _offers_back(result)

        result = await self._answer(hass, result, {FIELD_ACTION: NAV_ACTION_BACK})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_INIT

    async def test_the_options_flow_offers_the_product_list(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """Which product a device resolves against is the one thing to configure."""
        result = await hass.config_entries.options.async_init(entry.entry_id)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_INIT
        placeholders = result["description_placeholders"] or {}
        assert placeholders["current"] == SWITCH_PRODUCT

        result = await self._answer(hass, result, {FIELD_VENDOR: "espressif"})

        assert result["step_id"] == STEP_PROFILE_SELECT
        assert result["description_placeholders"]["current"] == SWITCH_PRODUCT
        assert result["description_placeholders"]["vendor"] == "Espressif"

    async def test_choosing_a_product_rebinds_it(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """Picking a product binds it at once; the other options stay."""
        result = await self._answer(
            hass, await self._at_products(hass, entry), {FIELD_PROFILE: SWITCH_KEY}
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["available_firmware"] == "1.0"
        binding = ProfileBinding.from_entry(entry)
        assert binding is not None
        assert binding.id == "espressif/switch"
        store = await async_get_store(hass)
        assert store.get(binding.id, binding.hash) is not None

    @pytest.mark.parametrize(
        "config",
        [
            {
                "info": {"model": MODEL_GENERIC},
                "devices": [{"name": "S", "type": "esp.device.switch", "params": []}],
            },
            {**SWITCH_CONFIG, "services": [{"name": "D", "type": "esp.service.x"}]},
            {
                "info": {"model": MODEL_GENERIC},
                "devices": [{"name": "S", "type": "esp.device.switch", "params": []}],
                "services": [{"name": "D", "type": "esp.service.x"}],
            },
        ],
        ids=["missing", "unknown", "both"],
    )
    async def test_an_incomplete_product_still_rebinds(
        self,
        hass: HomeAssistant,
        entry: MockConfigEntry,
        reported: MagicMock,
        config: dict[str, Any],
    ) -> None:
        """Skipped entities and unknown types are logged, not shown as a form."""
        reported.get_reported_config.return_value = (config, {})
        result = await self._answer(
            hass, await self._at_products(hass, entry), {FIELD_PROFILE: SWITCH_KEY}
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[OPTION_PROFILE]["id"] == "espressif/switch"

    async def _at_other_vendors_product(
        self, hass: HomeAssistant, entry: MockConfigEntry, own_config_dir: Path
    ) -> Any:
        """Open the options and pick a vendor's product naming another model.

        The node reports ``*``; the product names ``A1``.
        """
        _write_profile(own_config_dir, "bulb.yaml", _profile(model="A1"))
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await self._answer(hass, result, {FIELD_VENDOR: "acme"})
        assert result["step_id"] == STEP_PROFILE_SELECT
        return await self._answer(hass, result, {FIELD_PROFILE: "local:bulb.yaml"})

    async def test_a_product_the_node_is_not_cannot_be_bound(
        self,
        hass: HomeAssistant,
        entry: MockConfigEntry,
        reported: MagicMock,
        own_config_dir: Path,
    ) -> None:
        """Changing to the wrong product is refused the same way as choosing it.

        The node is not the model the product names: rebinding it would leave
        a device with no entities and nothing said about why.
        """
        result = await self._at_other_vendors_product(hass, entry, own_config_dir)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_DEVICE_MISMATCH
        assert result["description_placeholders"]["reported_model"] == MODEL_GENERIC

        result = await self._answer(hass, result, {})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_INIT
        assert entry.options[OPTION_PROFILE]["id"] == SWITCH_PRODUCT

    async def test_a_mismatch_read_again_is_shown_again(
        self,
        hass: HomeAssistant,
        entry: MockConfigEntry,
        reported: MagicMock,
        own_config_dir: Path,
    ) -> None:
        """The same fetch on the mismatch form leaves the binding alone."""
        result = await self._at_other_vendors_product(hass, entry, own_config_dir)
        assert result["step_id"] == STEP_DEVICE_MISMATCH

        again = await self._answer(hass, result, None)

        assert again["type"] is FlowResultType.FORM
        assert again["step_id"] == STEP_DEVICE_MISMATCH
        assert entry.options[OPTION_PROFILE]["id"] == SWITCH_PRODUCT

    async def test_a_node_that_has_not_reported_is_not_rebound_and_the_flow_aborts_with_node_unread(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        """No report, nothing to rehearse against: the binding is left as it was.

        Applying the choice blind would present a device that looks rebound
        and may build nothing. The flow ends saying why; the user tries again
        once the node has reported.
        """
        result = await self._answer(
            hass, await self._at_products(hass, entry), {FIELD_PROFILE: SWITCH_KEY}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NODE_UNREAD
        assert entry.options[OPTION_PROFILE]["id"] == SWITCH_PRODUCT

    async def test_a_rehearsal_that_raises_leaves_the_binding_alone(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """The options flow ends the same way as the config flow: nothing bound."""
        with patch(
            "custom_components.esp_weaver.flows.profile_choice.build_preview",
            side_effect=ValueError("malformed profile"),
        ):
            result = await self._answer(
                hass,
                await self._at_products(hass, entry),
                {FIELD_PROFILE: SWITCH_KEY},
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_SUMMARY_FAILED
        assert entry.options[OPTION_PROFILE]["id"] == SWITCH_PRODUCT

    async def test_an_unknown_product_is_refused_with_the_list_again(
        self, hass: HomeAssistant, entry: MockConfigEntry, reported: MagicMock
    ) -> None:
        """A stale dropdown must not bind the device to nothing."""
        result = await hass.config_entries.options.async_init(entry.entry_id)

        result = await self._answer(hass, result, {FIELD_VENDOR: "nobody"})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_INIT
        assert result["errors"] == {"base": ERROR_VENDOR_UNKNOWN}

        result = await self._answer(hass, result, {FIELD_VENDOR: "espressif"})
        result = await self._answer(hass, result, {FIELD_PROFILE: "nobody/nothing"})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROFILE_SELECT
        assert result["errors"] == {"base": ERROR_PROFILE_UNKNOWN}
        assert entry.options[OPTION_PROFILE]["id"] == SWITCH_PRODUCT
