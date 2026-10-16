# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Text rendered on the server, from the ``common`` translation category.

Text assembled at runtime - list labels, fragments in a step's description,
alert wording - cannot be translated by the frontend, so it is rendered in the
server's language. Every key is in the English catalogue, which is the fallback.
"""

from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from ..identity import DOMAIN
from .templating import render_template

COMMON_CATEGORY: Final = "common"


async def async_common_messages(hass: HomeAssistant, *keys: str) -> dict[str, str]:
    """Return the unrendered text of these common keys, by key."""
    catalogue = await async_get_translations(
        hass, hass.config.language, COMMON_CATEGORY, {DOMAIN}
    )
    prefix = f"component.{DOMAIN}.{COMMON_CATEGORY}."
    return {key: catalogue[prefix + key] for key in keys}


async def async_common_message(hass: HomeAssistant, key: str, **fields: str) -> str:
    """Return one common key rendered with its placeholders.

    Rendered the same way as profile text: a bare field name only.
    """
    return render_template((await async_common_messages(hass, key))[key], fields)
