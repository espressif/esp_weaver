# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""A GitHub that answers for one pull request or branch, for the tests."""

from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import yaml

from custom_components.esp_weaver.profiles.github import GITHUB_RAW, RepositoryRef
from custom_components.esp_weaver.profiles.index import (
    IndexSource,
    index_document,
    index_products,
    render_index,
)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
FORK = "acme/esp_weaver"


def product_doc(
    vendor: str = "acme",
    product: str = "plug",
    label: str = "Acme Plug",
    model: str = "T1",
    labels: dict[str, str] | None = None,
    vendor_label: str | None = None,
    vendor_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a small product document."""
    return {
        "version": 1,
        "vendor": vendor,
        **({"vendor_label": vendor_label} if vendor_label else {}),
        **({"vendor_labels": vendor_labels} if vendor_labels else {}),
        "product": product,
        "product_label": label,
        **({"product_labels": labels} if labels else {}),
        "model": model,
        "devices": [
            {
                "type": "esp.device.switch",
                "entities": [
                    {
                        "platform": "switch",
                        "name": "Switch",
                        "key": "state",
                        "attributes": {
                            "state": {
                                "param": "Power",
                                "data_type": "bool",
                                "properties": ["read", "write"],
                            }
                        },
                    }
                ],
            }
        ],
    }


def product_file(doc: dict[str, Any]) -> tuple[str, str]:
    """Return the document as the file the repository would hold it in."""
    return f"vendors/{doc['vendor']}/{doc['product']}.yaml", yaml.safe_dump(doc)


def pull_index(
    *docs: dict[str, Any],
    pull: int = 123,
    repo: str = FORK,
    commit: str = COMMIT,
    error: str | None = None,
) -> str:
    """Return the index CI would publish for a pull request holding these documents."""
    source = IndexSource(pull, repo, commit)
    if error is not None:
        return render_index(index_document([], source=source, error=error))
    products = index_products([product_file(doc) for doc in docs])
    return render_index(index_document(products, source=source))


def serve_ref(
    aioclient_mock: AiohttpClientMocker,
    *docs: dict[str, Any],
    pull: int = 123,
    repo: str = FORK,
    commit: str = COMMIT,
    error: str | None = None,
    index: str | None = None,
    published: bool = True,
    quota_spent: bool = False,
    missing: frozenset[str] = frozenset(),
) -> None:
    """Have GitHub answer for a pull request whose CI published an index.

    The index lists the documents, or carries ``error`` in their place, or is
    ``index`` verbatim. With ``published`` false there is none: the API and
    the raw file both answer 404. With ``quota_spent`` the API answers 403
    and only the raw file serves. A document whose product is in ``missing``
    is listed but its file not served.
    """
    ref = RepositoryRef(pull)
    text = (
        index
        if index is not None
        else pull_index(*docs, pull=pull, repo=repo, commit=commit, error=error)
    )
    if not published:
        aioclient_mock.get(ref.api_url, status=404)
        aioclient_mock.get(ref.raw_url, status=404)
    elif quota_spent:
        aioclient_mock.get(ref.api_url, status=403)
        aioclient_mock.get(ref.raw_url, text=text)
    else:
        aioclient_mock.get(ref.api_url, text=text)
    for doc in docs:
        path, body = product_file(doc)
        url = f"{GITHUB_RAW}/{repo}/{commit}/{path}"
        if doc["product"] in missing:
            aioclient_mock.get(url, status=404)
        else:
            aioclient_mock.get(url, text=body)
