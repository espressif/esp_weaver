# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The integration's identity: the leaf ``profiles/`` and ``iot/`` both import."""

from typing import Final

DOMAIN: Final = "esp_weaver"


def entity_unique_id(node_id: str, key: str) -> str:
    """Return the unique_id of one of a node's entities; built in one place so nothing drifts."""
    return f"{node_id}_{key}"
