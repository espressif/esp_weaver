# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the ESP-Weaver config flow."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esp_weaver.config_flow import (
    ESPWeaverConfigFlow,
    ESPWeaverOptionsFlow,
)
from custom_components.esp_weaver.const import (
    ABORT_NO_DEVICES,
    ABORT_NO_NEW_DEVICES,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_DEVICE,
    ERROR_INVALID_POP,
    ERROR_POP_REQUIRED,
    FIELD_SELECTED_DEVICE,
    STEP_DEVICE_SETUP,
    STEP_POP_INPUT,
)
from custom_components.esp_weaver.iot.cloud import CloudDeviceInfo
from custom_components.esp_weaver.iot.cloud.rmaker_cli_client import BLEDeviceInfo
from custom_components.esp_weaver.iot.entity_states import DiscoveredDevice
from custom_components.esp_weaver.iot.specs.events import DOMAIN
from custom_components.esp_weaver.iot.specs.keys import (
    CONF_CUSTOM_POP,
    CONF_NODE_ID,
    CONF_SECURITY_VERSION,
)

from .conftest import TEST_DEVICE_NAME, TEST_HOST, TEST_NODE_ID, TEST_POP, TEST_PORT


def _mock_security_manager(
    test_pop_result: bool | None = None,
    detect_security_result: dict[str, Any] | None = None,
    detect_security_error: Exception | None = None,
) -> MagicMock:
    """Create a mock security manager for testing.

    This helper centralizes the fragile _security_manager mocking pattern
    to make tests more maintainable if the implementation changes.

    Args:
        test_pop_result: Return value for test_pop_connection (None to skip mock)
        detect_security_result: Return value for detect_device_security
        detect_security_error: Exception to raise from detect_device_security
    """
    manager = MagicMock()
    if test_pop_result is not None:
        manager.test_pop_connection = AsyncMock(return_value=test_pop_result)
    if detect_security_error is not None:
        manager.detect_device_security = AsyncMock(side_effect=detect_security_error)
    elif detect_security_result is not None:
        manager.detect_device_security = AsyncMock(return_value=detect_security_result)
    return manager


class TestESPWeaverConfigFlowInit:
    """Test config flow initialization."""

    def test_config_flow_init(self) -> None:
        """Test config flow initialization with version info and required methods."""
        flow = ESPWeaverConfigFlow()
        assert flow is not None
        # Verify public version attributes
        assert flow.VERSION == 1
        assert flow.MINOR_VERSION == 1
        # Verify flow has required methods and async_step_user is callable
        assert hasattr(flow, "async_step_user")
        assert callable(flow.async_step_user)
        assert hasattr(flow, "async_step_device_setup")
        assert hasattr(flow, "async_step_pop_input")


class TestAsyncStepUser:
    """Test the user-initiated config flow."""

    async def test_user_step_shows_mode_selection(self, hass: HomeAssistant) -> None:
        """Test user step shows connection mode selection form first."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

    async def test_no_devices_found(self, hass: HomeAssistant) -> None:
        """Test we handle no devices found in local mode."""
        with patch(
            "custom_components.esp_weaver.config_flow.async_discover_devices",
            new_callable=AsyncMock,
            return_value=[],
        ):
            # First, init flow to get mode selection
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            # Then submit local mode selection
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "local"},
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES

    async def test_all_devices_configured(
        self,
        hass: HomeAssistant,
        mock_discovered_devices: list[dict[str, Any]],
    ) -> None:
        """Test we handle all devices already configured.

        This test uses direct flow instantiation to avoid complex HA setup mocking.
        """
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        # Simulate discovered devices that are all already configured
        existing_node_ids = {device[CONF_NODE_ID] for device in mock_discovered_devices}

        with (
            patch(
                "custom_components.esp_weaver.config_flow.async_discover_devices",
                new_callable=AsyncMock,
                return_value=mock_discovered_devices,
            ),
            patch.object(
                flow, "_get_existing_node_ids", return_value=existing_node_ids
            ),
        ):
            # Submit local mode selection to trigger device discovery
            result = await flow.async_step_user({"connection_mode": "local"})

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_NEW_DEVICES

    async def test_devices_found_shows_form(
        self,
        hass: HomeAssistant,
        mock_discovered_devices: list[dict[str, Any]],
    ) -> None:
        """Test showing device selection form when devices found."""
        with (
            patch(
                "custom_components.esp_weaver.config_flow.async_discover_devices",
                new_callable=AsyncMock,
                return_value=mock_discovered_devices,
            ),
            patch(
                "custom_components.esp_weaver.config_flow.ESPSecurityManager"
            ) as mock_manager_class,
        ):
            mock_manager = MagicMock()
            mock_manager.clear_cache = MagicMock()
            mock_manager.detect_device_security = AsyncMock(
                return_value={CONF_SECURITY_VERSION: 0}
            )
            mock_manager_class.return_value = mock_manager

            # First, init flow to get mode selection
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            # Then submit local mode selection
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "local"},
            )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_DEVICE_SETUP


class TestAsyncStepDeviceSetup:
    """Test device setup step."""

    async def test_invalid_device_selection(self, hass: HomeAssistant) -> None:
        """Test handling invalid device selection."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        flow._available_devices = [
            DiscoveredDevice(
                ip=TEST_HOST,
                node_id=TEST_NODE_ID,
                port=TEST_PORT,
                device_name=TEST_DEVICE_NAME,
            )
        ]

        result = await flow.async_step_device_setup(
            {FIELD_SELECTED_DEVICE: "invalid_node_id"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_INVALID_DEVICE}

    async def test_device_selection_no_security(self, hass: HomeAssistant) -> None:
        """Test device selection for device without security."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 0
        flow._available_devices = [device]

        with (
            patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
            patch.object(flow, "_abort_if_unique_id_configured"),
            patch.object(flow, "async_create_entry") as mock_create,
        ):
            mock_create.return_value = {"type": FlowResultType.CREATE_ENTRY}
            result = await flow.async_step_device_setup(
                {FIELD_SELECTED_DEVICE: TEST_NODE_ID}
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        # Verify entry was created with correct device configuration
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["title"] == TEST_DEVICE_NAME
        assert call_kwargs["data"][CONF_NODE_ID] == TEST_NODE_ID
        assert call_kwargs["data"]["host"] == TEST_HOST
        assert call_kwargs["data"]["port"] == TEST_PORT

    async def test_device_selection_with_security(self, hass: HomeAssistant) -> None:
        """Test device selection for device with security redirects to PoP."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._available_devices = [device]

        result = await flow.async_step_device_setup(
            {FIELD_SELECTED_DEVICE: TEST_NODE_ID}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_POP_INPUT


class TestAsyncStepPopInput:
    """Test PoP input step."""

    async def test_pop_input_no_selected_device(self, hass: HomeAssistant) -> None:
        """Test abort when no device is selected."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        flow._selected_device = None

        result = await flow.async_step_pop_input({CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ERROR_INVALID_DEVICE

    async def test_pop_required_when_empty(self, hass: HomeAssistant) -> None:
        """Test error when PoP is empty."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._selected_device = device

        result = await flow.async_step_pop_input({CONF_CUSTOM_POP: ""})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_POP_REQUIRED}

    async def test_pop_invalid(self, hass: HomeAssistant) -> None:
        """Test error when PoP is invalid."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._selected_device = device

        # Mock security manager to return False for PoP validation
        flow._security_manager = _mock_security_manager(test_pop_result=False)

        result = await flow.async_step_pop_input({CONF_CUSTOM_POP: "wrong_pop"})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_INVALID_POP}

    async def test_pop_valid(self, hass: HomeAssistant) -> None:
        """Test successful PoP validation."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._selected_device = device

        # Mock security manager to return True for PoP validation
        flow._security_manager = _mock_security_manager(test_pop_result=True)

        with (
            patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
            patch.object(flow, "_abort_if_unique_id_configured"),
            patch.object(flow, "async_create_entry") as mock_create,
        ):
            mock_create.return_value = {"type": FlowResultType.CREATE_ENTRY}
            result = await flow.async_step_pop_input({CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert device.security_info.get("pop") == TEST_POP

    async def test_pop_connection_error(self, hass: HomeAssistant) -> None:
        """Test error when connection fails during PoP validation."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._selected_device = device

        # Mock security manager to raise connection error
        manager = MagicMock()
        manager.test_pop_connection = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )
        flow._security_manager = manager

        result = await flow.async_step_pop_input({CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_CANNOT_CONNECT}

    async def test_pop_timeout_error(self, hass: HomeAssistant) -> None:
        """Test error when timeout occurs during PoP validation."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._selected_device = device

        # Mock security manager to raise timeout error
        manager = MagicMock()
        manager.test_pop_connection = AsyncMock(side_effect=TimeoutError("Timed out"))
        flow._security_manager = manager

        result = await flow.async_step_pop_input({CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_CANNOT_CONNECT}

    async def test_pop_os_error(self, hass: HomeAssistant) -> None:
        """Test error when OS error occurs during PoP validation."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device.security_version = 1
        flow._selected_device = device

        # Mock security manager to raise OS error
        manager = MagicMock()
        manager.test_pop_connection = AsyncMock(
            side_effect=OSError("Network unreachable")
        )
        flow._security_manager = manager

        result = await flow.async_step_pop_input({CONF_CUSTOM_POP: TEST_POP})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": ERROR_CANNOT_CONNECT}


class TestHelperMethods:
    """Test helper methods in config flow."""

    async def test_create_entry_from_device_none(self, hass: HomeAssistant) -> None:
        """Test _create_entry_from_device aborts when device is None."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        flow._selected_device = None

        result = await flow._create_entry_from_device()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ERROR_INVALID_DEVICE

    def test_find_device_by_node_id_found(self) -> None:
        """Test finding device by node ID."""
        flow = ESPWeaverConfigFlow()
        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        result = flow._find_device_by_node_id(TEST_NODE_ID)
        assert result == device

    def test_find_device_by_node_id_not_found(self) -> None:
        """Test finding device by node ID when not present."""
        flow = ESPWeaverConfigFlow()
        flow._available_devices = []

        result = flow._find_device_by_node_id(TEST_NODE_ID)
        assert result is None

    def test_find_device_by_node_id_none(self) -> None:
        """Test finding device with None node ID."""
        flow = ESPWeaverConfigFlow()
        result = flow._find_device_by_node_id(None)
        assert result is None

    def test_get_device_selection_schema(self) -> None:
        """Test device selection schema generation."""
        flow = ESPWeaverConfigFlow()
        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        schema = flow._get_device_selection_schema()
        assert schema is not None
        # Verify schema structure
        assert FIELD_SELECTED_DEVICE in schema.schema


class TestSecurityDetection:
    """Test security version detection."""

    async def test_detect_security_versions(self, hass: HomeAssistant) -> None:
        """Test parallel security detection."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device1 = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        device2 = DiscoveredDevice(
            ip="192.168.1.101",
            node_id="node_456",
            port=TEST_PORT,
            device_name="Device 2",
        )
        flow._available_devices = [device1, device2]

        flow._security_manager = _mock_security_manager(
            detect_security_result={CONF_SECURITY_VERSION: 0}
        )

        await flow._detect_security_versions()

        assert device1.security_version == 0
        assert device2.security_version == 0

    async def test_detect_security_versions_timeout(self, hass: HomeAssistant) -> None:
        """Test security detection with timeout."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        flow._security_manager = _mock_security_manager(
            detect_security_error=TimeoutError("Timeout")
        )

        # Should not raise, security_version defaults to 0 (no security)
        await flow._detect_security_versions()
        assert device.security_version == 0

    async def test_detect_security_versions_network_error(
        self, hass: HomeAssistant
    ) -> None:
        """Test security detection with network error."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        flow._security_manager = _mock_security_manager(
            detect_security_error=OSError("Network error")
        )

        # Should not raise, security_version defaults to 0 (no security)
        await flow._detect_security_versions()
        assert device.security_version == 0

    async def test_detect_security_versions_unexpected_exception(
        self, hass: HomeAssistant
    ) -> None:
        """Test security detection with unexpected exception is logged."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        # Use a non-network exception that will be caught by gather (not by inner except)
        flow._security_manager = _mock_security_manager(
            detect_security_error=ValueError("Unexpected error")
        )

        # Should not raise, but exception should be logged via line 155
        await flow._detect_security_versions()
        # Device security_version remains None when unexpected exception happens
        # (only network errors set it to 0)
        assert device.security_version is None

    async def test_detect_security_versions_wait_for_timeout(
        self, hass: HomeAssistant
    ) -> None:
        """Test security detection when asyncio.wait_for times out."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        flow._security_manager = _mock_security_manager(
            detect_security_result={CONF_SECURITY_VERSION: 0}
        )

        # Mock asyncio.wait_for to raise TimeoutError (simulating overall timeout)
        with patch("asyncio.wait_for", side_effect=TimeoutError("Overall timeout")):
            # Should not raise, just log a warning
            await flow._detect_security_versions()

        # Device security_version remains None when wait_for times out
        assert device.security_version is None

    async def test_detect_security_versions_cancelled(
        self, hass: HomeAssistant
    ) -> None:
        """Test security detection when task is cancelled."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        device = DiscoveredDevice(
            ip=TEST_HOST,
            node_id=TEST_NODE_ID,
            port=TEST_PORT,
            device_name=TEST_DEVICE_NAME,
        )
        flow._available_devices = [device]

        flow._security_manager = _mock_security_manager(
            detect_security_error=asyncio.CancelledError()
        )

        await flow._detect_security_versions()

        # CancelledError sets security_version to 0 before re-raising
        assert device.security_version == 0


class TestGetExistingNodeIds:
    """Test getting existing node IDs."""

    def test_get_existing_node_ids_empty(self, hass: HomeAssistant) -> None:
        """Test getting node IDs when none configured."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        hass.config_entries.async_entries = MagicMock(return_value=[])

        result = flow._get_existing_node_ids()
        assert result == set()

    def test_get_existing_node_ids_with_entries(self, hass: HomeAssistant) -> None:
        """Test getting node IDs when some configured."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        entry1 = MagicMock()
        entry1.unique_id = "node_1"
        entry2 = MagicMock()
        entry2.unique_id = "node_2"
        entry3 = MagicMock()
        entry3.unique_id = None  # Should be filtered out

        hass.config_entries.async_entries = MagicMock(
            return_value=[entry1, entry2, entry3]
        )

        result = flow._get_existing_node_ids()
        assert result == {"node_1", "node_2"}


class TestOptionsFlow:
    """Test options flow behavior."""

    def test_async_get_options_flow_returns_handler(self) -> None:
        """Ensure the options flow handler is returned."""
        config_entry = MagicMock()
        options_flow = ESPWeaverConfigFlow.async_get_options_flow(config_entry)

        assert isinstance(options_flow, ESPWeaverOptionsFlow)

    async def test_options_flow_abort(self, hass: HomeAssistant) -> None:
        """Options flow should abort when no options are available."""
        # Create a mock config entry with required attributes
        config_entry = MagicMock()
        config_entry.data = {
            CONF_NODE_ID: TEST_NODE_ID,
            "host": TEST_HOST,
            "port": TEST_PORT,
        }
        config_entry.options = {}

        # Use the config_entry fixture approach - instantiate normally
        options_flow = ESPWeaverOptionsFlow()
        options_flow.hass = hass

        result = await options_flow.async_step_init({})

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_options_available"


# =============================================================================
# Cloud Control Flow Tests
# =============================================================================


class TestCloudControlFlow:
    """Test cloud control flow."""

    async def test_cloud_mode_shows_login_form(self, hass: HomeAssistant) -> None:
        """Test that selecting cloud mode shows RainMaker login form."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(
                return_value={"logged_in": False}
            )
            mock_client_class.return_value = mock_client

            # Init flow
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            # Select cloud mode
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "rainmaker_login"

    async def test_cloud_login_success(self, hass: HomeAssistant) -> None:
        """Test successful RainMaker login."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(
                return_value={"logged_in": False}
            )
            mock_client.login = AsyncMock(return_value={"success": True})
            mock_client.get_rainmaker_devices = AsyncMock(return_value=[])
            mock_client_class.return_value = mock_client

            # Init flow and select cloud mode
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            # Submit login
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "cloud_action_select"

    async def test_cloud_login_failure(self, hass: HomeAssistant) -> None:
        """Test failed RainMaker login."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(
                return_value={"logged_in": False}
            )
            mock_client.login = AsyncMock(
                return_value={"success": False, "error": "Invalid credentials"}
            )
            mock_client_class.return_value = mock_client

            # Init flow and select cloud mode
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            # Submit invalid login
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "wrong_password"},
            )

            # Should show form again with error
            assert result["type"] is FlowResultType.FORM
            assert result["errors"]["base"] == "rainmaker_login_failed"

    async def test_cloud_already_logged_in(self, hass: HomeAssistant) -> None:
        """Test when already logged in to RainMaker."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(return_value=[])
            mock_client_class.return_value = mock_client

            # Init flow and select cloud mode
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            # Submit (even with credentials, should proceed since already logged in)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "cloud_action_select"


class TestCloudActionSelect:
    """Test cloud action selection step."""

    async def test_action_select_provision(self, hass: HomeAssistant) -> None:
        """Test selecting provision action starts BLE scan."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[
                    CloudDeviceInfo(
                        node_id="ABC123",
                        name="Test Device",
                    )
                ]
            )
            mock_client_class.return_value = mock_client

            # Init flow and login
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            # Select provision action
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "ble_scan"

    async def test_action_select_existing(self, hass: HomeAssistant) -> None:
        """Test selecting existing action loads cloud devices."""

        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[
                    CloudDeviceInfo(
                        node_id="ABC123",
                        name="Test Device",
                    )
                ]
            )
            mock_client_class.return_value = mock_client

            # Init flow and login
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            # Select existing action
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "existing"},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "cloud_device_select"


class TestLoadCloudDevices:
    """Test _load_and_select_cloud_devices."""

    async def test_no_cloud_devices(self, hass: HomeAssistant) -> None:
        """Test abort when no cloud devices found."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(return_value=[])
            mock_client_class.return_value = mock_client

            # Init flow and login
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            # Select existing action - should abort because no devices
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "existing"},
            )

            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "no_devices_found"


class TestBleScan:
    """Test BLE scan step."""

    async def test_ble_scan_valid_device_name(self, hass: HomeAssistant) -> None:
        """Test valid device name proceeds to WiFi config."""

        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client_class.return_value = mock_client

            # Init flow, login, select provision
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            # Enter device name
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "wifi_config"

    async def test_ble_scan_empty_device_name(self, hass: HomeAssistant) -> None:
        """Test empty device name shows error."""

        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client_class.return_value = mock_client

            # Init flow, login, select provision
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            # Enter empty device name
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": ""},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "ble_scan"
            assert result["errors"]["base"] == "invalid_device_selection"

    async def test_ble_scan_nonstandard_device_name(self, hass: HomeAssistant) -> None:
        """Test BLE scan with non-standard device name (warns but proceeds)."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.login = AsyncMock(return_value={"success": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client_class.return_value = mock_client

            # Init flow, login, select provision
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            # Enter non-standard device name (should warn but proceed)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "CUSTOM_DEVICE"},
            )
            # Should still proceed to wifi_config
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "wifi_config"


class TestWifiConfig:
    """Test WiFi configuration step."""

    async def test_wifi_config_form_shown(self, hass: HomeAssistant) -> None:
        """Test WiFi config form is shown after BLE scan."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.login = AsyncMock(return_value={"success": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client_class.return_value = mock_client

            # Init flow, login, select provision, enter device name
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            # Verify we reach the wifi_config step
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "wifi_config"


class TestProvisioning:
    """Test provisioning step."""

    async def test_provisioning_failure(self, hass: HomeAssistant) -> None:
        """Test failed provisioning shows error."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.login = AsyncMock(return_value={"success": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client.provision_device = AsyncMock(
                return_value={"success": False, "error": "BLE connection failed"}
            )
            mock_client_class.return_value = mock_client

            # Init flow through to provisioning
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "wifi_ssid": "TestWiFi",
                    "wifi_password": "wifi_pass",
                    "device_pop": "",
                },
            )

            # Confirm provisioning
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["errors"]["base"] == "provisioning_failed"


class TestCloudDeviceSelect:
    """Test cloud device selection step."""

    async def test_select_cloud_device_success(self, hass: HomeAssistant) -> None:
        """Test selecting cloud device creates entry."""

        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[
                    CloudDeviceInfo(
                        node_id="ABC123",
                        name="Test Device",
                        connected=True,
                    )
                ]
            )
            mock_client_class.return_value = mock_client

            # Init flow through to device select
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "existing"},
            )
            # Select device
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"selected_device": "ABC123"},
            )

            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["node_id"] == "ABC123"
            assert result["data"]["connection_mode"] == "cloud"
            assert "Cloud" in result["title"]

    async def test_select_invalid_cloud_device(self, hass: HomeAssistant) -> None:
        """Test selecting invalid device shows error (device removed during selection).

        The SelectSelector validates values against its options list before the
        handler runs, so we call the step handler directly to simulate a race
        condition where the device list changes between form display and submission.
        """
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        flow._cloud_devices = [
            CloudDeviceInfo(node_id="ABC123", name="Test Device", connected=True)
        ]

        result = await flow.async_step_cloud_device_select(
            {"selected_device": "NONEXISTENT_DEVICE"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == "invalid_device_selection"


class TestCloudLoginEdgeCases:
    """Test cloud login edge cases."""

    async def test_cloud_login_no_credentials_via_direct_call(
        self, hass: HomeAssistant
    ) -> None:
        """Test cloud login when not logged in and no credentials provided.

        Tests line 188: errors["base"] = ERROR_RAINMAKER_NOT_LOGGED_IN
        """
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(
                return_value={"logged_in": False}
            )
            mock_client_class.return_value = mock_client

            # Directly call the step with empty credentials
            result = await flow.async_step_rainmaker_login(
                user_input={"email": "", "password": ""}
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "rainmaker_login"
            assert result["errors"]["base"] == "rainmaker_not_logged_in"


class TestCloudDeviceLoadEdgeCases:
    """Test cloud device loading edge cases."""

    async def test_all_cloud_devices_already_configured(
        self, hass: HomeAssistant
    ) -> None:
        """Test when all cloud devices are already configured."""
        # First, create an existing entry
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"node_id": "ABC123", "connection_mode": "cloud"},
            unique_id="ABC123",
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[
                    CloudDeviceInfo(
                        node_id="ABC123",  # Same as existing entry
                        name="Test Device",
                    )
                ]
            )
            mock_client_class.return_value = mock_client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "existing"},
            )

            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "no_new_devices_found"


class TestWifiConfigEdgeCases:
    """Test WiFi configuration edge cases."""

    async def test_wifi_config_empty_ssid(self, hass: HomeAssistant) -> None:
        """Test WiFi config with empty SSID shows error."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client_class.return_value = mock_client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            # Submit with empty ssid
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"wifi_ssid": "", "wifi_password": "pass", "device_pop": ""},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "wifi_config"
            assert result["errors"]["base"] == "wifi_ssid_required"


class TestProvisioningSuccess:
    """Test successful provisioning flow."""

    async def test_provisioning_success_with_bound_device(
        self, hass: HomeAssistant
    ) -> None:
        """Test successful provisioning with bound device."""
        with patch(
            "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[CloudDeviceInfo(node_id="ABC123", name="Test Device")]
            )
            mock_client.provision_device = AsyncMock(
                return_value={"success": True, "node_id": "NEW123", "bound": True}
            )
            mock_client_class.return_value = mock_client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "wifi_ssid": "TestWiFi",
                    "wifi_password": "wifi_pass",
                    "device_pop": "",
                },
            )

            # After provisioning success, get_rainmaker_devices returns the new device
            mock_client.get_rainmaker_devices = AsyncMock(
                return_value=[
                    CloudDeviceInfo(
                        node_id="NEW123",
                        name="Provisioned Device",
                        connected=True,
                    )
                ]
            )

            # Trigger provisioning step
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {},
            )

            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["node_id"] == "NEW123"

    async def test_provisioning_success_unbound_device(
        self, hass: HomeAssistant
    ) -> None:
        """Test provisioning success with unbound device that appears later."""
        with (
            patch(
                "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
            ) as mock_client_class,
            patch(
                "custom_components.esp_weaver.config_flow.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(return_value=[])
            mock_client.provision_device = AsyncMock(
                return_value={"success": True, "node_id": "NEW123", "bound": False}
            )
            mock_client_class.return_value = mock_client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "wifi_ssid": "TestWiFi",
                    "wifi_password": "wifi_pass",
                    "device_pop": "",
                },
            )

            # Simulate device appearing in cloud after first poll
            call_count = 0

            async def get_devices_side_effect():
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    return [
                        CloudDeviceInfo(
                            node_id="NEW123",
                            name="New Device",
                            connected=True,
                        )
                    ]
                return []

            mock_client.get_rainmaker_devices = AsyncMock(
                side_effect=get_devices_side_effect
            )

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {},
            )

            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["node_id"] == "NEW123"

    async def test_provisioning_device_not_found_in_cloud(
        self, hass: HomeAssistant
    ) -> None:
        """Test provisioning when device never appears in cloud."""
        with (
            patch(
                "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
            ) as mock_client_class,
            patch(
                "custom_components.esp_weaver.config_flow.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            # Always return empty list - device never appears
            mock_client.get_rainmaker_devices = AsyncMock(return_value=[])
            mock_client.provision_device = AsyncMock(
                return_value={"success": True, "node_id": "NEW123", "bound": False}
            )
            mock_client_class.return_value = mock_client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "wifi_ssid": "TestWiFi",
                    "wifi_password": "wifi_pass",
                    "device_pop": "",
                },
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "provisioning"
            assert result["errors"]["base"] == "provisioning_failed"

    async def test_provisioning_multiple_new_devices_no_node_id(
        self, hass: HomeAssistant
    ) -> None:
        """Test provisioning when multiple new devices appear without node_id."""
        with (
            patch(
                "custom_components.esp_weaver.config_flow.RainMakerCLIClient"
            ) as mock_client_class,
            patch(
                "custom_components.esp_weaver.config_flow.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_client = MagicMock()
            mock_client.check_login_status = AsyncMock(return_value={"logged_in": True})
            mock_client.get_rainmaker_devices = AsyncMock(return_value=[])
            # Provisioning succeeds but without node_id
            mock_client.provision_device = AsyncMock(
                return_value={"success": True, "node_id": None, "bound": False}
            )
            mock_client_class.return_value = mock_client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"connection_mode": "cloud"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"email": "test@example.com", "password": "password123"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"action": "provision"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"device_name": "PROV_123456"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "wifi_ssid": "TestWiFi",
                    "wifi_password": "wifi_pass",
                    "device_pop": "",
                },
            )

            # Multiple new devices appear (simulates edge case)
            call_count = 0

            async def get_devices_multi():
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    return [
                        CloudDeviceInfo(node_id="DEV1", name="Device 1"),
                        CloudDeviceInfo(node_id="DEV2", name="Device 2"),
                    ]
                return []

            mock_client.get_rainmaker_devices = AsyncMock(side_effect=get_devices_multi)

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {},
            )

            # Should select first device when multiple found without node_id
            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["node_id"] == "DEV1"


class TestDirectFlowCalls:
    """Test direct flow method calls for edge cases."""

    async def test_load_cloud_devices_no_client(self, hass: HomeAssistant) -> None:
        """Test _load_and_select_cloud_devices when cli_client is None."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        flow._cli_client = None  # No client

        result = await flow._load_and_select_cloud_devices()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "rainmaker_not_logged_in"

    async def test_ble_scan_no_client(self, hass: HomeAssistant) -> None:
        """Test async_step_ble_scan when cli_client is None."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        flow._cli_client = None  # No client

        result = await flow.async_step_ble_scan(user_input=None)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "rainmaker_not_logged_in"

    async def test_provisioning_no_client_or_device(self, hass: HomeAssistant) -> None:
        """Test async_step_provisioning when cli_client or ble_device is None."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass
        flow._cli_client = None  # No client
        flow._selected_ble_device = None  # No device

        result = await flow.async_step_provisioning(user_input=None)

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "invalid_device_selection"

    async def test_load_cloud_devices_api_exception(self, hass: HomeAssistant) -> None:
        """Test _load_and_select_cloud_devices when API raises exceptions."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        mock_client = MagicMock()
        mock_client.get_rainmaker_devices = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )
        flow._cli_client = mock_client

        with patch(
            "custom_components.esp_weaver.config_flow.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await flow._load_and_select_cloud_devices()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_devices_found"

    async def test_provisioning_back_action(self, hass: HomeAssistant) -> None:
        """Test provisioning step returns to wifi_config on back action."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        flow._cli_client = MagicMock()
        flow._selected_ble_device = BLEDeviceInfo(name="PROV_123456")

        result = await flow.async_step_provisioning(user_input={"action": "back"})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "wifi_config"

    async def test_provisioning_poll_api_exception(self, hass: HomeAssistant) -> None:
        """Test provisioning polling when get_rainmaker_devices raises."""
        flow = ESPWeaverConfigFlow()
        flow.hass = hass

        mock_client = MagicMock()
        mock_client.provision_device = AsyncMock(
            return_value={"success": True, "node_id": "NEW123", "bound": False}
        )
        mock_client.get_rainmaker_devices = AsyncMock(
            side_effect=RuntimeError("Cloud API error")
        )
        flow._cli_client = mock_client
        flow._selected_ble_device = BLEDeviceInfo(name="PROV_123456")
        flow._wifi_ssid = "TestWiFi"
        flow._wifi_password = "pass"
        flow._device_pop = ""

        with patch(
            "custom_components.esp_weaver.config_flow.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await flow.async_step_provisioning(user_input=None)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "provisioning"
        assert result["errors"]["base"] == "provisioning_failed"
