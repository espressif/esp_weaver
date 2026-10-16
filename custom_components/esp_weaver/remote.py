# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""ESP-Weaver remote entity, driven entirely by device profiles."""

from collections.abc import Iterable
import logging
from typing import Any, Final

from homeassistant.components.remote import (
    ATTR_ACTIVITY,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError

from .entity import ATTR_STATE, ESPWeaverPoweredEntity
from .helpers.discovery import binding_platform_setup
from .identity import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Writes are HTTP requests the ESP32 takes best one at a time.
PARALLEL_UPDATES: Final = 1

# Profile attribute names this platform understands. Everything else the
# profile declares lands in extra_attributes as read-only detail.
ATTR_CURRENT_ACTIVITY: Final = "current_activity"


class ESPWeaverRemote(ESPWeaverPoweredEntity, RemoteEntity):
    """A scene remote whose parameter mapping comes from a device profile.

    Power and activity travel in one write.
    """

    def _configure(self) -> None:
        """Set up the remote."""
        binding = self._binding

        if self._has(ATTR_CURRENT_ACTIVITY):
            if activities := self._options(ATTR_CURRENT_ACTIVITY):
                self._attr_activity_list = activities
                self._attr_supported_features = RemoteEntityFeature.ACTIVITY
            else:
                _LOGGER.warning(
                    "Device %s reported no activities for %s, so none are offered",
                    self._node_id,
                    binding.name,
                )

    @property
    def current_activity(self) -> str | None:
        """Return the scene the remote is running, if it has scenes."""
        return self._value(ATTR_CURRENT_ACTIVITY, str)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the remote on, into a given activity when one was asked for."""
        values: dict[str, Any] = {ATTR_STATE: True}
        activity = kwargs.get(ATTR_ACTIVITY)
        if activity is not None and self._has(ATTR_CURRENT_ACTIVITY):
            values[ATTR_CURRENT_ACTIVITY] = activity
        await self._apply(values)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the remote off."""
        await self._apply({ATTR_STATE: False})

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Refuse to send a command: RainMaker has no notion of one.

        Home Assistant registers this action for every remote with no feature
        to gate it on, so it is refused with a message rather than a traceback.

        Raises:
            HomeAssistantError: Always.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="remote_no_commands",
            translation_placeholders={"entity": self._binding.name},
        )

    async def async_learn_command(self, **kwargs: Any) -> None:
        """Refuse to learn a command. See :meth:`async_send_command`.

        Raises:
            HomeAssistantError: Always.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="remote_cannot_learn",
            translation_placeholders={"entity": self._binding.name},
        )

    async def async_delete_command(self, **kwargs: Any) -> None:
        """Refuse to delete a command. See :meth:`async_send_command`.

        Raises:
            HomeAssistantError: Always.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="remote_no_stored_commands",
            translation_placeholders={"entity": self._binding.name},
        )


async_setup_entry = binding_platform_setup(Platform.REMOTE, ESPWeaverRemote)
