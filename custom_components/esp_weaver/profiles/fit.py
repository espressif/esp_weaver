# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Whether a product is for a device, judged from the model the device reports.

The product is picked before the device, so the device list is narrowed to what
the product fits, and the check is repeated against the node config once the
device is read. One thing the device reports is held against the product:

``model``
    The device's model must be the one the product names, byte for byte - what
    tells a vendor's A1 from its A2. Espressif's per-type reference profiles
    name ``"*"``, so they fit a device whose firmware reports ``"*"`` as its
    model, and nothing else: a vendor's device is listed under the vendor's own
    product, never under a reference one.

Device types are not part of the identity. They are settled once the node is
read, entity by entity: a device type the node reports but the product does
not declare is listed as unknown, and a device type the product declares but
the node lacks builds nothing (see ``resolver``).

Before the handshake the model comes from the mDNS TXT record; after it, from
the node config. A device whose record carries no model (older firmware) is not
judged at all: such firmware reports its project name as the model, which says
nothing about the product it is.
"""

from typing import Any, Final

from .product import ProductCandidate

# Why a device is not the product's, for the flow to say.
MISFIT_MODEL: Final = "model"


def reported_model(node_config: dict[str, Any]) -> str | None:
    """Return the model a node config reports, or None when it gives none."""
    info = node_config.get("info") or {}
    model = info.get("model") if isinstance(info, dict) else None
    return str(model) if model else None


def misfit(product: ProductCandidate, model: str | None) -> str | None:
    """Return why the device is not the product's, or None when it is.

    ``MISFIT_MODEL`` when it reports another model than the product names, or
    none.
    """
    if model == product.model:
        return None
    return MISFIT_MODEL
