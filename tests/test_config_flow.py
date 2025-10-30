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

    async def test_no_devices_found(self, hass: HomeAssistant) -> None:
        """Test we handle no devices found."""
        with patch(
            "custom_components.esp_weaver.config_flow.async_discover_devices",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
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
            result = await flow.async_step_user(None)

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

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
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
