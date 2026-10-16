# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for choosing a product: the catalog, the store, bindings and repairs."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import yaml

from custom_components.esp_weaver import _async_binding_changed
from custom_components.esp_weaver.const import CONF_NODE_ID
from custom_components.esp_weaver.data import domain_data
from custom_components.esp_weaver.helpers.entries import entry_for_node
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.profiles import mirror as mirror_module
from custom_components.esp_weaver.profiles.catalog import (
    ProfileCatalog,
    async_fetch_content,
    async_get_catalog,
    async_refresh_catalog,
)
from custom_components.esp_weaver.profiles.loader import CUSTOM_PROFILE_DIR
from custom_components.esp_weaver.profiles.mirror import (
    INDEX_FETCH_TIMEOUT,
    PROFILE_SOURCES,
    STORAGE_KEY as MIRROR_STORAGE_KEY,
    CatalogError,
    async_get_mirror,
)
from custom_components.esp_weaver.profiles.product import (
    CATEGORY_CLIMATE,
    CATEGORY_COVERS,
    CATEGORY_LIGHTING,
    CATEGORY_MULTI,
    CATEGORY_ORDER,
    CATEGORY_OTHER,
    CATEGORY_SENSORS,
    CATEGORY_SWITCHES,
    SOURCE_BUILTIN,
    SOURCE_CACHED,
    SOURCE_LOCAL,
    SOURCE_REPO,
    ProductCandidate,
    category_of,
)
from custom_components.esp_weaver.profiles.schema import parse_profile_document
from custom_components.esp_weaver.profiles.selection import (
    ISSUE_DATA_HASH,
    ISSUE_DATA_PRODUCT,
    ISSUE_DATA_SOURCE,
    ISSUE_UPDATE_PREFIX,
    OPTION_PROFILE,
    DeviceIdentity,
    ProfileBinding,
    ProfileMissingError,
    async_bind,
    async_prune_store,
    async_registry_for_candidate,
    async_registry_for_entry,
    async_sync_binding_issues,
    entries_to_update,
    options_with_binding,
    update_issue_id,
)
from custom_components.esp_weaver.profiles.store import (
    KEY_SOURCE,
    KEY_STORED_AT,
    PRUNE_GRACE,
    STORAGE_KEY,
    ProfileStore,
    async_get_store,
    record_key,
)
from custom_components.esp_weaver.repairs import (
    ABORT_FETCH_FAILED,
    ABORT_GONE,
    STEP_CONFIRM,
    ProfileUpdateFlow,
    async_create_fix_flow,
)
from tests.conftest import TEST_HOST, TEST_PORT

REPO = PROFILE_SOURCES[0]

# A shipped product, for asserting that the shipped ones were read.
SWITCH_PRODUCT = "espressif/switch"

SWITCH_CONFIG: dict[str, Any] = {
    "info": {"project_name": "switch"},
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


def _doc(
    device_type: str = "esp.device.switch",
    entity_name: str = "Switch",
    **top: Any,
) -> dict[str, Any]:
    """Return a small product document."""
    return {
        "version": 1,
        "model": "T1",
        "vendor": "acme",
        "product": "plug",
        "product_label": "Acme Plug",
        **top,
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


def _candidate(
    source: str = SOURCE_LOCAL, doc: dict[str, Any] | None = None
) -> ProductCandidate:
    """Return a candidate with content, as a scan would produce it."""
    return ProductCandidate.from_parsed(
        parse_profile_document(doc or _doc(), "acme/plug"), source=source
    )


def _entry(
    hass: HomeAssistant,
    node_id: str = "node-1",
    binding: ProfileBinding | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Add a config entry for a local node, optionally bound."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Device {node_id}",
        data={CONF_HOST: TEST_HOST, CONF_PORT: TEST_PORT, CONF_NODE_ID: node_id},
        options=options_with_binding(options, binding) if binding else options or {},
        unique_id=node_id,
    )
    entry.add_to_hass(hass)
    return entry


def _issue_ids(hass: HomeAssistant) -> list[str]:
    """Return every open issue of the integration."""
    return sorted(
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN
    )


def _backdate(store: ProfileStore, candidate: ProductCandidate) -> None:
    """Make a stored record old enough for a prune to consider it."""
    old = datetime.now(UTC) - PRUNE_GRACE - timedelta(minutes=1)
    store._products[record_key(candidate.id, candidate.hash)][KEY_STORED_AT] = (
        old.isoformat()
    )


def _index(*candidates: ProductCandidate, path_of: str = "vendors/{id}.yaml") -> str:
    """Return an index document listing these candidates."""
    return json.dumps(
        {
            "version": 1,
            "products": [
                {
                    "vendor": c.vendor,
                    "product": c.product,
                    "product_label": c.label,
                    "path": path_of.format(id=c.id),
                    "hash": c.hash,
                    "category": c.category,
                }
                for c in candidates
            ],
        }
    )


def _serve(
    aioclient_mock: AiohttpClientMocker, *docs: dict[str, Any], base: str = REPO
) -> list[ProductCandidate]:
    """Have a source serve an index listing these documents, and the documents."""
    candidates = [_candidate(SOURCE_REPO, doc) for doc in docs]
    aioclient_mock.get(base + "index.json", text=_index(*candidates))
    for candidate, doc in zip(candidates, docs, strict=True):
        aioclient_mock.get(
            base + f"vendors/{candidate.id}.yaml", text=yaml.safe_dump(doc)
        )
    return candidates


# Captured before the autouse fixture in conftest replaces it.
_REAL_FETCH_INDEX = mirror_module.async_fetch_index


async def _pick(
    hass: HomeAssistant, product: str = "acme/plug", *, fails: bool = False
) -> ProfileCatalog:
    """Do what picking a repository product in the flow does, then relist.

    The fetch is expected to fail when ``fails``; the error is swallowed.
    """
    [listed] = [
        c
        for c in (await async_get_catalog(hass)).by_id(product)
        if c.source == SOURCE_REPO
    ]
    try:
        await async_fetch_content(hass, listed)
    except CatalogError:
        assert fails
    else:
        assert not fails
    return await async_refresh_catalog(hass, online=False)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the catalog go to the (mocked) network for the repository."""
    monkeypatch.setattr(mirror_module, "async_fetch_index", _REAL_FETCH_INDEX)


class TestBinding:
    """The option a config entry carries."""

    def test_a_binding_round_trips_through_the_option(self) -> None:
        """What is written is what is read back."""
        binding = ProfileBinding("acme/plug", SOURCE_LOCAL, "abc")
        entry = MockConfigEntry(
            domain=DOMAIN, options=options_with_binding({"other": 1}, binding)
        )

        assert entry.options["other"] == 1
        assert ProfileBinding.from_entry(entry) == binding

    def test_the_option_holds_id_source_and_hash_only(self) -> None:
        """Nothing in it marks a binding as tentative; every binding is a choice."""
        option = ProfileBinding("acme/plug", SOURCE_LOCAL, "abc").as_option()
        assert option == {"id": "acme/plug", "source": SOURCE_LOCAL, "hash": "abc"}

    @pytest.mark.parametrize(
        "options",
        [
            {},
            {OPTION_PROFILE: "acme/plug"},
            {OPTION_PROFILE: {"id": "x"}},
            # A binding that does not say where it came from cannot be
            # watched for updates; it is not a binding.
            {OPTION_PROFILE: {"id": "acme/plug", "hash": "abc"}},
        ],
    )
    def test_an_entry_without_a_usable_binding_has_none(
        self, options: dict[str, Any]
    ) -> None:
        """An entry with no binding, or a damaged one, reads as unbound."""
        entry = MockConfigEntry(domain=DOMAIN, options=options)
        assert ProfileBinding.from_entry(entry) is None

    def test_for_candidate_takes_id_source_and_hash(self) -> None:
        """The binding remembers the content, and where it was found."""
        candidate = _candidate(SOURCE_BUILTIN)
        binding = ProfileBinding.for_candidate(candidate)
        assert (binding.id, binding.source, binding.hash) == (
            "acme/plug",
            SOURCE_BUILTIN,
            candidate.hash,
        )

    async def test_a_binding_from_a_kept_copy_watches_the_original_source(
        self, hass: HomeAssistant
    ) -> None:
        """Choosing the [cached] copy of a shipped profile follows the shipped one."""
        original = _candidate(SOURCE_BUILTIN)
        store = await async_get_store(hass)
        await store.async_put(original)
        kept = store.get(original.id, original.hash)
        assert kept is not None
        assert kept.source == SOURCE_CACHED
        assert kept.origin == SOURCE_BUILTIN
        assert kept.watched_source == SOURCE_BUILTIN

        binding = ProfileBinding.for_candidate(kept)

        assert binding.source == SOURCE_BUILTIN
        assert binding.hash == original.hash

    def test_entries_are_found_by_node_and_by_product(
        self, hass: HomeAssistant
    ) -> None:
        """Both lookups the runtime needs."""
        candidate = _candidate()
        bound = _entry(hass, "n1", ProfileBinding.for_candidate(candidate))
        _entry(hass, "n2")

        assert entry_for_node(hass, "n1") is bound
        assert entry_for_node(hass, "nope") is None
        assert entries_to_update(hass, "acme/plug", SOURCE_LOCAL, "newer") == [bound]
        assert entries_to_update(hass, "acme/other", SOURCE_LOCAL, "newer") == []

    def test_only_entries_from_that_source_and_not_yet_on_the_content_update(
        self, hass: HomeAssistant
    ) -> None:
        """A binding is to one copy; another copy's updates are not its business."""
        candidate = _candidate()
        from_local = _entry(hass, "n1", ProfileBinding.for_candidate(candidate))
        _entry(hass, "n2", ProfileBinding("acme/plug", SOURCE_BUILTIN, "old"))
        _entry(hass, "n3", ProfileBinding("acme/plug", SOURCE_LOCAL, "newer"))

        assert entries_to_update(hass, "acme/plug", SOURCE_LOCAL, "newer") == [
            from_local
        ]
        assert entries_to_update(hass, "acme/plug", SOURCE_REPO, "newer") == []

    def test_the_update_issue_is_named_after_product_and_source(self) -> None:
        """One issue per copy of a product, and the id says which copy."""
        assert update_issue_id("acme/plug", SOURCE_LOCAL) == (
            f"{ISSUE_UPDATE_PREFIX}{SOURCE_LOCAL}__acme__plug"
        )


class TestStore:
    """The content devices are bound to, under ``.storage``."""

    async def test_put_then_get_returns_the_content_as_cached(
        self, hass: HomeAssistant
    ) -> None:
        """Whatever the source was, what comes back out is a cached copy."""
        candidate = _candidate(
            SOURCE_REPO,
            _doc(
                product_labels={"zh-Hans": "Acme 插头"},
                vendor_label="Acme",
                vendor_labels={"zh-Hans": "阿克米"},
            ),
        )
        store = await async_get_store(hass)

        await store.async_put(candidate)

        stored = store.get(candidate.id, candidate.hash)
        assert stored is not None
        assert stored.source == SOURCE_CACHED
        assert stored.content == candidate.content
        assert stored.id == candidate.id
        assert stored.labels == {"zh-Hans": "Acme 插头"}
        assert stored.localized_vendor("en") == "Acme"
        assert stored.localized_vendor("zh-Hans") == "阿克米"
        assert [c.hash for c in store.candidates()] == [candidate.hash]

    async def test_the_store_survives_a_restart(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """A fresh store object reads what the last one wrote."""
        candidate = _candidate()
        await (await async_get_store(hass)).async_put(candidate)

        record = hass_storage[STORAGE_KEY]["data"]["products"][
            f"{candidate.id}@{candidate.hash}"
        ]
        assert record[KEY_SOURCE] == SOURCE_LOCAL

        fresh = ProfileStore(hass)
        await fresh.async_load()
        reread = fresh.get(candidate.id, candidate.hash)
        assert reread is not None
        assert reread.origin == SOURCE_LOCAL

    async def test_a_record_the_schema_now_refuses_is_not_offered(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Content stored under an older schema is revalidated on load."""
        good = _candidate()
        await (await async_get_store(hass)).async_put(good)
        products = hass_storage[STORAGE_KEY]["data"]["products"]
        products["acme/old@0123456789abcdef"] = {
            **products[f"{good.id}@{good.hash}"],
            "id": "acme/old",
            "content": {"version": 1, "devices": "not a list"},
        }

        fresh = ProfileStore(hass)
        await fresh.async_load()

        assert [c.id for c in fresh.candidates()] == [good.id]
        assert fresh.get("acme/old", "0123456789abcdef") is None
        assert "acme/old@0123456789abcdef" in caplog.text
        assert "choose the product again" in caplog.text

        # A save made afterwards writes the refused record back untouched.
        await fresh.async_prune(keep={(good.id, good.hash)})
        await fresh.async_put(_candidate(doc=_doc(product_label="Other", model="Z9")))
        written = hass_storage[STORAGE_KEY]["data"]["products"]
        assert written["acme/old@0123456789abcdef"]["content"] == {
            "version": 1,
            "devices": "not a list",
        }

    async def test_a_hash_already_held_is_left_alone(self, hass: HomeAssistant) -> None:
        """Same hash, same content; the first record's label is kept."""
        store = await async_get_store(hass)
        first = _candidate(doc=_doc(product_label="First"))
        again = _candidate(doc=_doc(product_label="Second"))
        assert first.hash == again.hash

        await store.async_put(first)
        await store.async_put(again)

        stored = store.get(first.id, first.hash)
        assert stored is not None
        assert stored.label == "First"

    async def test_two_products_with_the_same_mapping_are_kept_apart(
        self, hass: HomeAssistant
    ) -> None:
        """A vendor's copy of a bundled profile is its own product, not the original.

        The hash covers the mapping alone, so the copy hashes the same; it
        must still come back under its own id, label and vendor.
        """
        store = await async_get_store(hass)
        original = _candidate(doc=_doc(product_label="Espressif Light"))
        copy = ProductCandidate.from_parsed(
            parse_profile_document(
                _doc(vendor="acme", product="mylight", product_label="Acme Light"),
                "acme/mylight",
            ),
            source=SOURCE_LOCAL,
        )
        assert original.hash == copy.hash
        assert original.id != copy.id

        await store.async_put(original)
        await store.async_put(copy)

        stored_copy = store.get(copy.id, copy.hash)
        assert stored_copy is not None
        assert stored_copy.id == "acme/mylight"
        assert stored_copy.label == "Acme Light"
        assert stored_copy.vendor == "acme"
        stored_original = store.get(original.id, original.hash)
        assert stored_original is not None
        assert stored_original.label == "Espressif Light"
        # Both are offered, and the catalog can tell them apart.
        assert len({c.key for c in store.candidates()}) == 2

    async def test_prune_keeps_only_the_pairs_still_bound(
        self, hass: HomeAssistant
    ) -> None:
        """Pruning is by product and hash together, like the bindings are."""
        store = await async_get_store(hass)
        original = _candidate()
        copy = ProductCandidate.from_parsed(
            parse_profile_document(_doc(vendor="acme", product="other"), "acme/other"),
            source=SOURCE_LOCAL,
        )
        await store.async_put(original)
        await store.async_put(copy)
        _backdate(store, original)
        _backdate(store, copy)

        await store.async_prune({(copy.id, copy.hash)})

        assert store.get(original.id, original.hash) is None
        assert store.get(copy.id, copy.hash) is not None

    async def test_a_freshly_stored_record_survives_a_prune(
        self, hass: HomeAssistant
    ) -> None:
        """Content is stored a moment before the entry pointing at it exists.

        A prune in that moment must not take it away again.
        """
        store = await async_get_store(hass)
        fresh = _candidate()
        await store.async_put(fresh)

        await store.async_prune(set())

        assert store.get(fresh.id, fresh.hash) is not None

    @pytest.mark.parametrize("stored_at", [None, "not a date", 12345])
    async def test_a_record_without_a_usable_date_is_prunable(
        self, hass: HomeAssistant, stored_at: Any
    ) -> None:
        """A record from before dates were written cannot claim to be fresh."""
        store = await async_get_store(hass)
        candidate = _candidate()
        await store.async_put(candidate)
        record = store._products[record_key(candidate.id, candidate.hash)]
        if stored_at is None:
            del record[KEY_STORED_AT]
        else:
            record[KEY_STORED_AT] = stored_at

        await store.async_prune(set())

        assert store.get(candidate.id, candidate.hash) is None

    async def test_a_candidate_without_content_cannot_be_stored(
        self, hass: HomeAssistant
    ) -> None:
        """A listing is not something a device can be bound to."""
        store = await async_get_store(hass)
        listed = ProductCandidate("acme", "plug", "Plug", SOURCE_REPO, "abc")
        with pytest.raises(ValueError):
            await store.async_put(listed)

    async def test_prune_keeps_what_is_referenced(self, hass: HomeAssistant) -> None:
        """Content no entry points at goes; content one does stays."""
        keep = _candidate(doc=_doc(entity_name="Keep"))
        drop = _candidate(doc=_doc(entity_name="Drop"))
        store = await async_get_store(hass)
        await store.async_put(keep)
        await store.async_put(drop)
        _backdate(store, keep)
        _backdate(store, drop)
        _entry(hass, "n1", ProfileBinding.for_candidate(keep))

        await async_prune_store(hass)
        await async_prune_store(hass)  # Nothing left to drop: a no-op.

        assert [c.hash for c in store.candidates()] == [keep.hash]


class TestCatalog:
    """Assembling the list from the four sources."""

    @pytest.fixture(autouse=True)
    def _config_dir(self, own_config_dir: Path) -> None:
        """Write profiles somewhere this test owns."""

    @staticmethod
    def _write_local(hass: HomeAssistant, doc: dict[str, Any], name: str) -> None:
        custom_dir = Path(hass.config.path(CUSTOM_PROFILE_DIR))
        custom_dir.mkdir(exist_ok=True)
        (custom_dir / name).write_text(yaml.safe_dump(doc))

    async def test_the_store_contributes_what_was_downloaded(
        self, hass: HomeAssistant
    ) -> None:
        """A product bound once is offered again, offline, marked as cached."""
        candidate = _candidate(SOURCE_REPO)
        await (await async_get_store(hass)).async_put(candidate)

        catalog = await async_refresh_catalog(hass, online=False)

        [offered] = catalog.by_id("acme/plug")
        assert offered.source == SOURCE_CACHED
        assert offered.key == f"cached:{candidate.id}@{candidate.hash}"

    async def test_a_local_file_and_the_cached_copy_are_both_listed(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing is chosen for the user: both copies are offered, the local first."""
        await (await async_get_store(hass)).async_put(_candidate(SOURCE_REPO))
        await hass.async_add_executor_job(
            self._write_local, hass, _doc(product_label="Mine"), "plug.yaml"
        )

        catalog = await async_refresh_catalog(hass, online=False)

        copies = catalog.by_id("acme/plug")
        assert [(c.source, c.label) for c in copies] == [
            (SOURCE_LOCAL, "Mine"),
            (SOURCE_CACHED, "Acme Plug"),
        ]
        assert copies[0].key == "local:plug.yaml"

    async def test_two_local_files_with_one_id_are_two_entries(
        self, hass: HomeAssistant
    ) -> None:
        """Same vendor and product in two files: the user tells them apart by path."""
        await hass.async_add_executor_job(
            self._write_local, hass, _doc(product_label="One"), "one.yaml"
        )
        await hass.async_add_executor_job(
            self._write_local, hass, _doc(product_label="Two"), "two.yaml"
        )

        catalog = await async_refresh_catalog(hass, online=False)

        assert [(c.label, c.location) for c in catalog.by_id("acme/plug")] == [
            ("One", "one.yaml"),
            ("Two", "two.yaml"),
        ]

    async def test_sorted_groups_by_vendor_then_category_then_product(
        self, hass: HomeAssistant
    ) -> None:
        """A vendor's list reads group by group; copies of one product sit together."""
        catalog = ProfileCatalog(
            candidates={
                c.key: c
                for c in (
                    ProductCandidate("zeta", "a", "A", SOURCE_LOCAL, "1", "a.yaml"),
                    ProductCandidate(
                        "acme",
                        "z",
                        "zed",
                        SOURCE_LOCAL,
                        "2",
                        "z.yaml",
                        category=CATEGORY_LIGHTING,
                    ),
                    ProductCandidate(
                        "acme",
                        "b",
                        "Bee",
                        SOURCE_REPO,
                        "3",
                        "v/b.yaml",
                        category=CATEGORY_SWITCHES,
                    ),
                    ProductCandidate(
                        "acme",
                        "b",
                        "Bee",
                        SOURCE_LOCAL,
                        "4",
                        "b.yaml",
                        category=CATEGORY_SWITCHES,
                    ),
                    ProductCandidate("acme", "q", "Q", SOURCE_REPO, "5", "v/q.yaml"),
                )
            }
        )
        # Lighting before switches (the fixed group order), an index entry with
        # no group last; between copies, local before repository.
        assert [(c.id, c.source) for c in catalog.sorted("acme")] == [
            ("acme/z", SOURCE_LOCAL),
            ("acme/b", SOURCE_LOCAL),
            ("acme/b", SOURCE_REPO),
            ("acme/q", SOURCE_REPO),
        ]
        assert [c.id for c in catalog.sorted()][-1] == "zeta/a"
        assert catalog.vendors() == {"acme": 4, "zeta": 1}

    async def test_describe_lists_products_and_problems(
        self, hass: HomeAssistant
    ) -> None:
        """Diagnostics gets the list and the files that could not be read."""
        await hass.async_add_executor_job(
            self._write_local,
            hass,
            {"version": 1, "model": "T1", "devices": "no"},
            "bad.yaml",
        )

        described = (await async_refresh_catalog(hass, online=False)).describe()

        assert described["online"] is False
        assert described["repository_synced_at"] is None
        assert SWITCH_PRODUCT in [p["id"] for p in described["products"]]
        assert [p["kind"] for p in described["problems"]] == ["invalid"]

    async def test_concurrent_first_use_builds_once(self, hass: HomeAssistant) -> None:
        """Setup and diagnostics asking together must not scan twice."""
        first, second = await asyncio.gather(
            async_get_catalog(hass), async_get_catalog(hass)
        )
        assert first is second


class TestRepositoryCopy:
    """The local copy of the repository.

    Its index is refreshed online, a file downloaded when its product is
    picked, and everything is listed from offline.
    """

    @pytest.fixture(autouse=True)
    def _config_dir(self, own_config_dir: Path) -> None:
        """Write profiles somewhere this test owns."""

    async def test_going_online_lists_the_index_and_picking_a_product_downloads(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The refresh costs the index alone; a file comes when its product is picked."""
        [listed] = _serve(aioclient_mock, _doc())

        online = await async_refresh_catalog(hass, online=True)

        assert aioclient_mock.call_count == 1
        assert online.online is True
        assert online.index_error == ""
        assert online.repository_synced_at is not None
        [offered] = online.by_id("acme/plug")
        assert offered.source == SOURCE_REPO
        assert offered.content is None
        assert offered.hash == listed.hash
        assert offered.location == "vendors/acme/plug.yaml"
        assert offered.key == "repo:vendors/acme/plug.yaml"
        assert offered.url == REPO + "vendors/acme/plug.yaml"
        assert offered.category == CATEGORY_SWITCHES

        picked = await _pick(hass)

        assert aioclient_mock.call_count == 2
        [held] = picked.by_id("acme/plug")
        assert held.content is not None
        assert held.hash == listed.hash
        assert held.key == offered.key
        assert picked.online is False
        assert picked.repository_synced_at == online.repository_synced_at

    async def test_only_the_picked_product_s_file_is_downloaded(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """Picking one product fetches one file; the vendor's others stay listings."""
        _serve(
            aioclient_mock,
            _doc(),
            _doc(product="other"),
            _doc(vendor="other", product="thing"),
        )
        await async_refresh_catalog(hass, online=True)

        catalog = await _pick(hass, "acme/plug")

        assert aioclient_mock.call_count == 2
        [plug] = catalog.by_id("acme/plug")
        [other] = catalog.by_id("acme/other")
        [thing] = catalog.by_id("other/thing")
        assert plug.content is not None
        assert other.content is None
        assert thing.content is None

    async def test_a_product_held_at_the_promised_hash_costs_nothing_to_pick(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The second pick, and every pick offline after it, makes no request."""
        _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        aioclient_mock.clear_requests()
        aioclient_mock.get(REPO + "vendors/acme/plug.yaml", exc=TimeoutError())

        catalog = await _pick(hass)

        assert aioclient_mock.call_count == 0
        [held] = catalog.by_id("acme/plug")
        assert held.content is not None

    async def test_a_held_product_the_index_moved_past_is_fetched_when_picked(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """Picking gives the version the index promises, not the one held."""
        [old] = _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        aioclient_mock.clear_requests()
        [new] = _serve(aioclient_mock, _doc(entity_name="Relay"))
        # The sync would refresh it too; go past that to the pick itself.
        mirror = await async_get_mirror(hass)
        mirror._products["acme/plug"]["hash"] = new.hash
        [stale] = (await async_refresh_catalog(hass, online=False)).by_id("acme/plug")
        assert stale.hash == old.hash

        fetched = await async_fetch_content(hass, stale)

        assert fetched.hash == new.hash
        assert aioclient_mock.call_count == 1
        [held] = (await async_refresh_catalog(hass, online=False)).by_id("acme/plug")
        assert held.hash == new.hash

    async def test_a_stale_product_whose_fetch_fails_is_picked_as_held(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Offline, the version held is what the device gets, with a warning."""
        [old] = _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        mirror = await async_get_mirror(hass)
        mirror._products["acme/plug"]["hash"] = "0000000000000000"
        aioclient_mock.clear_requests()
        aioclient_mock.get(REPO + "vendors/acme/plug.yaml", exc=TimeoutError())
        [stale] = (await async_refresh_catalog(hass, online=False)).by_id("acme/plug")

        fetched = await async_fetch_content(hass, stale)

        assert fetched.hash == old.hash
        assert fetched.content is not None
        assert "stays at its held version" in caplog.text

    async def test_the_copy_is_read_back_after_a_restart(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """A household that downloaded a product once picks it offline for good."""
        _serve(aioclient_mock, _doc(), _doc(vendor="other", product="thing"))
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        domain_data(hass).mirror = None  # As a restart would leave it.

        catalog = await async_refresh_catalog(hass, online=False)

        assert aioclient_mock.call_count == 2
        [offered] = catalog.by_id("acme/plug")
        assert offered.content is not None
        [other] = catalog.by_id("other/thing")
        assert other.content is None
        assert catalog.repository_synced_at is not None

    async def test_an_unchanged_product_is_not_downloaded_again(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The second refresh costs the index and nothing else; nor does picking again."""
        _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)

        await async_refresh_catalog(hass, online=True)
        await _pick(hass)

        assert aioclient_mock.call_count == 3

    async def test_a_changed_product_replaces_the_copy_s_version(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """A held product whose hash moved is refreshed with the index, unasked.

        That is what raises the update repairs for the devices bound to it.
        """
        [old] = _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        aioclient_mock.clear_requests()
        [new] = _serve(aioclient_mock, _doc(entity_name="Relay"))

        catalog = await async_refresh_catalog(hass, online=True)

        assert new.hash != old.hash
        assert aioclient_mock.call_count == 2
        [offered] = catalog.by_id("acme/plug")
        assert offered.hash == new.hash
        assert offered.content is not None
        assert offered.content["devices"][0]["entities"][0]["name"] == "Relay"

    async def test_a_changed_product_nobody_holds_is_only_relisted(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The index's new word is taken; the file waits for someone to want it."""
        _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)
        aioclient_mock.clear_requests()
        [new] = _serve(aioclient_mock, _doc(entity_name="Relay"))

        catalog = await async_refresh_catalog(hass, online=True)

        assert aioclient_mock.call_count == 1
        [offered] = catalog.by_id("acme/plug")
        assert offered.hash == new.hash
        assert offered.content is None

    async def test_a_product_no_longer_listed_leaves_the_copy(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The copy follows the index; a withdrawn product is not offered any more."""
        _serve(aioclient_mock, _doc(), _doc(product="other"))
        await async_refresh_catalog(hass, online=True)
        aioclient_mock.clear_requests()
        _serve(aioclient_mock, _doc())

        catalog = await async_refresh_catalog(hass, online=True)

        assert catalog.by_id("acme/plug") != []
        assert catalog.by_id("acme/other") == []

    async def test_a_file_that_fails_to_download_keeps_the_copy_s_version(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """One bad download neither loses the product nor stops the others."""
        [old] = _serve(aioclient_mock, _doc(), _doc(product="other"))[:1]
        await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        aioclient_mock.clear_requests()
        changed = _candidate(SOURCE_REPO, _doc(entity_name="Relay"))
        other = _candidate(SOURCE_REPO, _doc(product="other", entity_name="Relay"))
        aioclient_mock.get(REPO + "index.json", text=_index(changed, other))
        aioclient_mock.get(REPO + "vendors/acme/plug.yaml", status=404)
        aioclient_mock.get(
            REPO + "vendors/acme/other.yaml",
            text=yaml.safe_dump(_doc(product="other", entity_name="Relay")),
        )

        catalog = await async_refresh_catalog(hass, online=True)

        [plug] = catalog.by_id("acme/plug")
        assert plug.hash == old.hash
        assert plug.content is not None
        [refreshed] = catalog.by_id("acme/other")
        assert refreshed.hash == other.hash
        assert "1 profiles could not be downloaded" in catalog.index_error
        assert "HTTP 404" in catalog.index_error
        # The stale copy is offered until the file comes; picking the product
        # tries once more.
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            REPO + "vendors/acme/plug.yaml",
            text=yaml.safe_dump(_doc(entity_name="Relay")),
        )
        [plug] = (await _pick(hass)).by_id("acme/plug")
        assert plug.hash == changed.hash

    async def test_a_product_whose_download_fails_stays_listed_without_content(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The listing stands and says why; picking it tries the file once more."""
        listed = _candidate(SOURCE_REPO)
        aioclient_mock.get(REPO + "index.json", text=_index(listed))
        aioclient_mock.get(REPO + "vendors/acme/plug.yaml", exc=TimeoutError())
        await async_refresh_catalog(hass, online=True)

        catalog = await _pick(hass, fails=True)

        [offered] = catalog.by_id("acme/plug")
        assert offered.content is None
        with pytest.raises(CatalogError, match="TimeoutError"):
            await async_fetch_content(hass, offered)

    async def test_a_file_that_does_not_match_the_index_is_not_taken(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """An index that lies about its files is not one to bind devices to."""
        listed = ProductCandidate(
            "acme", "plug", "Acme Plug", SOURCE_REPO, "0000000000000000"
        )
        aioclient_mock.get(REPO + "index.json", text=_index(listed))
        aioclient_mock.get(REPO + "vendors/acme/plug.yaml", text=yaml.safe_dump(_doc()))
        await async_refresh_catalog(hass, online=True)

        catalog = await _pick(hass, fails=True)

        [offered] = catalog.by_id("acme/plug")
        assert offered.content is None
        with pytest.raises(CatalogError, match="does not match the index"):
            await async_fetch_content(hass, offered)

    async def test_a_refresh_that_fails_leaves_the_copy_as_it_was(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """No internet: the list shows the repository as last seen, and says so."""
        _serve(aioclient_mock, _doc())
        first = await async_refresh_catalog(hass, online=True)
        await _pick(hass)
        aioclient_mock.clear_requests()
        for base in PROFILE_SOURCES:
            aioclient_mock.get(base + "index.json", exc=TimeoutError())

        catalog = await async_refresh_catalog(hass, online=True)

        [kept] = catalog.by_id("acme/plug")
        assert kept.content is not None
        assert "TimeoutError" in catalog.index_error
        assert catalog.repository_synced_at == first.repository_synced_at
        # The failure is remembered by the offline builds that follow, so the
        # form coming back from a later step still carries the notice.
        offline = await async_refresh_catalog(hass, online=False)
        assert offline.index_error == catalog.index_error

    async def test_a_second_source_is_tried_when_the_first_fails(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        monkeypatch: pytest.MonkeyPatch,
        repository: None,
    ) -> None:
        """Adding a base to ``PROFILE_SOURCES`` is all a fallback takes."""
        second = "https://mirror.invalid/esp-weaver-profiles/"
        monkeypatch.setattr(mirror_module, "PROFILE_SOURCES", (REPO, second))
        aioclient_mock.get(REPO + "index.json", status=500)
        _serve(aioclient_mock, _doc(), base=second)
        await async_refresh_catalog(hass, online=True)

        catalog = await _pick(hass)

        [offered] = catalog.by_id("acme/plug")
        assert offered.url is not None
        assert offered.url.startswith(second)
        assert offered.content is not None

    async def test_listing_the_repository_gives_each_source_a_short_timeout(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        repository: None,
    ) -> None:
        """A form waiting on the list must not wait one full download timeout per source.

        Each source is asked with the short index timeout, and one that fails
        does not stop the next from being tried.
        """
        second = "https://mirror.invalid/esp-weaver-profiles/"
        monkeypatch.setattr(mirror_module, "PROFILE_SOURCES", (REPO, second))
        asked: list[tuple[str, Any]] = []

        async def _refuse(
            _hass: HomeAssistant, url: str, _limit: int, timeout: Any = None
        ) -> str:
            asked.append((url, timeout))
            raise CatalogError(f"TimeoutError fetching {url}")

        monkeypatch.setattr(mirror_module, "_fetch_text", _refuse)

        catalog = await async_refresh_catalog(hass, online=True)

        assert [url for url, _ in asked] == [REPO + "index.json", second + "index.json"]
        assert all(timeout is INDEX_FETCH_TIMEOUT for _, timeout in asked)
        assert catalog.by_id(SWITCH_PRODUCT) != []
        assert "TimeoutError" in catalog.index_error

    @pytest.mark.parametrize(
        "body",
        [
            "not json",
            json.dumps({"version": 2, "products": []}),
            json.dumps({"version": 1, "products": [{"vendor": "acme"}]}),
            json.dumps(
                {
                    "version": 1,
                    "products": [
                        {
                            "vendor": "acme",
                            "product": "plug",
                            "path": "vendors/acme/plug.yaml",
                            # A hash is exactly as long as the schema computes.
                            "hash": "0123456789abcdef0",
                        }
                    ],
                }
            ),
        ],
    )
    async def test_an_unusable_index_is_reported_not_raised(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
        body: str,
    ) -> None:
        """The list still comes up, saying the repository could not be read."""
        for base in PROFILE_SOURCES:
            aioclient_mock.get(base + "index.json", text=body)

        catalog = await async_refresh_catalog(hass, online=True)

        assert catalog.by_id(SWITCH_PRODUCT) != []
        assert catalog.by_id("acme/plug") == []
        assert "not usable" in catalog.index_error

    async def test_an_oversized_index_is_refused(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        monkeypatch: pytest.MonkeyPatch,
        repository: None,
    ) -> None:
        """Whatever is that big is not the index."""
        monkeypatch.setattr(mirror_module, "MAX_INDEX_SIZE", 16)
        for base in PROFILE_SOURCES:
            aioclient_mock.get(base + "index.json", text="x" * 64)

        catalog = await async_refresh_catalog(hass, online=True)

        assert "larger than" in catalog.index_error

    async def test_a_leading_slash_in_a_path_is_tolerated(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The file is fetched from under the base either way."""
        [listed] = _serve(aioclient_mock, _doc())
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            REPO + "index.json", text=_index(listed, path_of="/vendors/{id}.yaml")
        )
        aioclient_mock.get(REPO + "vendors/acme/plug.yaml", text=yaml.safe_dump(_doc()))
        await async_refresh_catalog(hass, online=True)

        catalog = await _pick(hass)

        [offered] = catalog.by_id("acme/plug")
        assert offered.url == REPO + "vendors/acme/plug.yaml"
        assert offered.content is not None

    async def test_a_cached_copy_and_the_repository_s_are_both_offered(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        repository: None,
    ) -> None:
        """The kept copy and the repository entry are two choices, even when equal."""
        candidate = _candidate(SOURCE_REPO)
        await (await async_get_store(hass)).async_put(candidate)
        _serve(aioclient_mock, _doc())
        await async_refresh_catalog(hass, online=True)

        catalog = await _pick(hass)

        assert [c.source for c in catalog.by_id("acme/plug")] == [
            SOURCE_CACHED,
            SOURCE_REPO,
        ]
        # Each copy is found by its own source; the same content from a source
        # that does not offer it is not.
        listed = catalog.by_hash(candidate.hash, "acme/plug", SOURCE_REPO)
        assert listed is not None
        assert listed.source == SOURCE_REPO
        kept = catalog.by_hash(candidate.hash, "acme/plug", SOURCE_CACHED)
        assert kept is not None
        assert kept.source == SOURCE_CACHED
        assert catalog.by_hash(candidate.hash, "acme/plug", SOURCE_LOCAL) is None
        assert catalog.by_hash(candidate.hash, "acme/other", SOURCE_REPO) is None
        assert catalog.by_hash("nope", "acme/plug", SOURCE_REPO) is None

    async def test_a_record_the_schema_refuses_is_dropped_with_a_warning(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Content another release wrote is dropped, the listing kept.

        A record that is not one at all is dropped whole.
        """
        good = _candidate(SOURCE_REPO)
        bad = _candidate(SOURCE_REPO, _doc(product="bad"))
        hass_storage[MIRROR_STORAGE_KEY] = {
            "version": 1,
            "minor_version": 1,
            "key": MIRROR_STORAGE_KEY,
            "data": {
                "synced_at": "2026-01-01T00:00:00+00:00",
                "products": {
                    "acme/plug": {
                        "vendor": "acme",
                        "product": "plug",
                        "product_label": "Acme Plug",
                        "model": "T1",
                        "path": "vendors/acme/plug.yaml",
                        "url": REPO + "vendors/acme/plug.yaml",
                        "hash": good.hash,
                        "content": good.content,
                    },
                    "acme/bad": {
                        "vendor": "acme",
                        "product": "bad",
                        "product_label": "Acme Plug",
                        "model": "T1",
                        "path": "vendors/acme/bad.yaml",
                        "url": REPO + "vendors/acme/bad.yaml",
                        "hash": bad.hash,
                        "content": {"version": 1},
                    },
                    "acme/worse": "not a record",
                    "acme/worst": {"content": {"version": 1}},
                },
            },
        }

        mirror = await async_get_mirror(hass)

        offered = {c.id: c for c in mirror.candidates()}
        assert sorted(offered) == ["acme/bad", "acme/plug"]
        assert offered["acme/plug"].content is not None
        assert offered["acme/bad"].content is None
        assert offered["acme/bad"].hash == bad.hash
        assert mirror.synced_at == "2026-01-01T00:00:00+00:00"
        assert "acme/bad is dropped" in caplog.text
        assert "acme/worse is dropped" in caplog.text
        assert "acme/worst is dropped" in caplog.text


class TestCategory:
    """Which group of a vendor's list a product sits in, read off its entities."""

    @staticmethod
    def _doc_with(*platform_sets: list[str]) -> dict[str, Any]:
        """Return a document with one device block per platform list."""
        return {
            "version": 1,
            "model": "T1",
            "vendor": "acme",
            "product": "thing",
            "devices": [
                {
                    "type": f"esp.device.t{i}",
                    "entities": [{"platform": p} for p in platforms],
                }
                for i, platforms in enumerate(platform_sets)
            ],
        }

    def test_one_type_is_grouped_by_its_defining_platform(self) -> None:
        """The platform that gives the product its purpose decides, not the extras."""
        assert category_of(self._doc_with(["light"])) == CATEGORY_LIGHTING
        assert category_of(self._doc_with(["cover", "lock"])) == CATEGORY_COVERS
        assert category_of(self._doc_with(["switch", "humidifier"])) == CATEGORY_CLIMATE
        assert category_of(self._doc_with(["number", "sensor", "switch"])) == (
            CATEGORY_SWITCHES
        )
        assert category_of(self._doc_with(["number", "sensor"])) == CATEGORY_SENSORS

    def test_several_types_are_a_multi_device_product(self) -> None:
        """Whatever the types are: the user has a node made of several things."""
        assert category_of(self._doc_with(["light"], ["switch"])) == CATEGORY_MULTI

    def test_nothing_that_places_it_is_other(self) -> None:
        """No entities, or only helpers such as numbers and selects."""
        assert category_of(self._doc_with([])) == CATEGORY_OTHER
        assert category_of(self._doc_with(["number", "select"])) == CATEGORY_OTHER
        assert (
            category_of({"version": 1, "model": "T1", "devices": []}) == CATEGORY_OTHER
        )

    def test_the_shipped_products_land_where_the_user_would_look(self) -> None:
        """A validated scan produces the group; every group has a fixed place."""
        candidate = _candidate(SOURCE_BUILTIN)
        assert candidate.category == CATEGORY_SWITCHES
        assert candidate.category_rank == CATEGORY_ORDER.index(CATEGORY_SWITCHES)
        unplaced = ProductCandidate("v", "p", "P", SOURCE_REPO, "1", category="odd")
        assert unplaced.category_rank == len(CATEGORY_ORDER)


class TestFetchContent:
    """Downloading a listed product before it can be bound."""

    async def test_content_in_hand_is_returned_as_is(self, hass: HomeAssistant) -> None:
        """A local or cached candidate has nothing to fetch."""
        candidate = _candidate()
        assert await async_fetch_content(hass, candidate) is candidate

    async def test_a_listing_without_a_url_cannot_be_fetched(
        self, hass: HomeAssistant
    ) -> None:
        """Defensive: the index always gives a path, but the type allows None."""
        with pytest.raises(CatalogError, match="neither content nor a URL"):
            await async_fetch_content(
                hass, ProductCandidate("acme", "plug", "Plug", SOURCE_REPO, "abc")
            )

    async def test_a_file_candidate_without_content_is_refused(
        self, hass: HomeAssistant
    ) -> None:
        """Defensive: a local or cached candidate is never built without content."""
        with pytest.raises(CatalogError, match="has no content"):
            await async_fetch_content(
                hass, ProductCandidate("acme", "plug", "Plug", SOURCE_LOCAL, "abc")
            )

    async def test_the_download_is_validated_and_checked_against_the_index(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """What comes back is a candidate with content and the promised hash."""
        real = _candidate(SOURCE_REPO)
        listed = ProductCandidate(
            "acme", "plug", "Acme Plug", SOURCE_REPO, real.hash, url=REPO + "p.yaml"
        )
        aioclient_mock.get(REPO + "p.yaml", text=yaml.safe_dump(_doc()))

        fetched = await async_fetch_content(hass, listed)

        assert fetched.content is not None
        assert fetched.hash == real.hash
        assert fetched.source == SOURCE_REPO
        assert fetched.url == REPO + "p.yaml"

    async def test_a_download_that_differs_from_the_index_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An index that lies about its files is not one to bind devices to."""
        listed = ProductCandidate(
            "acme", "plug", "Plug", SOURCE_REPO, "0000000000000000", url=REPO + "p"
        )
        aioclient_mock.get(REPO + "p", text=yaml.safe_dump(_doc()))

        with pytest.raises(CatalogError, match="does not match the index"):
            await async_fetch_content(hass, listed)

    async def test_a_download_that_is_another_product_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A file's own vendor and product win; listed under another id, it lies."""
        other = _candidate(SOURCE_REPO, _doc(vendor="acme", product="other"))
        listed = ProductCandidate(
            "acme", "plug", "Plug", SOURCE_REPO, other.hash, url=REPO + "p"
        )
        aioclient_mock.get(
            REPO + "p", text=yaml.safe_dump(_doc(vendor="acme", product="other"))
        )

        with pytest.raises(CatalogError, match="got acme/other@"):
            await async_fetch_content(hass, listed)

    async def test_a_download_that_is_not_a_profile_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Schema errors come back as catalog errors, with the reason."""
        listed = ProductCandidate(
            "acme", "plug", "Plug", SOURCE_REPO, "abc", url=REPO + "p"
        )
        aioclient_mock.get(
            REPO + "p",
            text="version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n",
        )

        with pytest.raises(CatalogError):
            await async_fetch_content(hass, listed)

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            ({"status": 404}, "HTTP 404"),
            ({"exc": TimeoutError()}, "TimeoutError"),
            ({"content": b"\xff\xfe"}, "not UTF-8"),
        ],
    )
    async def test_a_failed_download_says_why(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        response: dict[str, Any],
        message: str,
    ) -> None:
        """The error names the URL and the failure."""
        listed = ProductCandidate(
            "acme", "plug", "Plug", SOURCE_REPO, "abc", url=REPO + "p"
        )
        aioclient_mock.get(REPO + "p", **response)

        with pytest.raises(CatalogError, match=message):
            await async_fetch_content(hass, listed)


class TestRegistryForEntry:
    """What a device resolves against, given its binding."""

    async def test_a_bound_entry_resolves_against_the_stored_content(
        self, hass: HomeAssistant
    ) -> None:
        """The store, not the file: the file may have changed since."""
        candidate = _candidate(doc=_doc(entity_name="Stored"))
        entry = _entry(hass, "n1")
        await async_bind(hass, entry, candidate)

        registry = await async_registry_for_entry(hass, entry)

        profile = registry.get_device("esp.device.switch")
        assert profile is not None
        assert profile.entities[0].name == "Stored"
        assert registry.product_id == "acme/plug"
        assert _issue_ids(hass) == []

    async def test_the_registry_is_built_once_per_hash(
        self, hass: HomeAssistant
    ) -> None:
        """Two devices on the same content share one registry."""
        candidate = _candidate()
        first = _entry(hass, "n1")
        second = _entry(hass, "n2")
        await async_bind(hass, first, candidate)
        await async_bind(hass, second, candidate)

        assert (await async_registry_for_entry(hass, first)) is (
            await async_registry_for_entry(hass, second)
        )

    async def test_a_candidate_without_content_cannot_build_a_registry(
        self, hass: HomeAssistant
    ) -> None:
        """A listing has to be fetched first."""
        with pytest.raises(ValueError):
            await async_registry_for_candidate(
                hass, ProductCandidate("acme", "plug", "Plug", SOURCE_REPO, "abc")
            )

    async def test_an_unbound_entry_fails_setup(self, hass: HomeAssistant) -> None:
        """An unbound entry is not quietly given a product."""
        entry = _entry(hass, "n1")

        with pytest.raises(ProfileMissingError) as caught:
            await async_registry_for_entry(hass, entry)

        assert isinstance(caught.value, ConfigEntryError)
        assert caught.value.translation_key == "profile_unbound"
        assert caught.value.translation_placeholders == {"device": entry.title}
        assert _issue_ids(hass) == []

    async def test_a_binding_whose_store_is_gone_fails_setup_even_with_a_copy_on_disk(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """A file of the same id is not what the user confirmed; nothing stands in."""
        (own_config_dir / CUSTOM_PROFILE_DIR).mkdir()
        (own_config_dir / CUSTOM_PROFILE_DIR / "plug.yaml").write_text(
            yaml.safe_dump(_doc(entity_name="On disk"))
        )
        binding = ProfileBinding("acme/plug", SOURCE_LOCAL, "gone0000gone0000")
        entry = _entry(hass, "n1", binding)

        with pytest.raises(ProfileMissingError) as caught:
            await async_registry_for_entry(hass, entry)

        assert caught.value.translation_key == "profile_missing"
        assert caught.value.translation_placeholders == {
            "device": entry.title,
            "product": "acme/plug",
            "hash": "gone0000gone0000",
        }
        assert caught.value.binding == binding
        assert _issue_ids(hass) == []

    async def test_a_binding_nothing_can_find_fails_setup(
        self, hass: HomeAssistant
    ) -> None:
        """Not a shipped product either: the device is re-added, not propped up."""
        entry = _entry(hass, "n1", ProfileBinding("acme/vanished", SOURCE_REPO, "x"))

        with pytest.raises(ProfileMissingError):
            await async_registry_for_entry(hass, entry)

    async def test_binding_makes_the_entry_resolvable(
        self, hass: HomeAssistant
    ) -> None:
        """Once a product is chosen the entry resolves against exactly that."""
        entry = _entry(hass, "n1")
        candidate = _candidate()

        binding = await async_bind(hass, entry, candidate)

        assert ProfileBinding.from_entry(entry) == binding
        assert (await async_get_store(hass)).get(binding.id, binding.hash) is not None
        registry = await async_registry_for_entry(hass, entry)
        assert registry.product_id == candidate.id


class TestSyncBindingIssues:
    """Repairs about bindings, kept in step with the catalog."""

    async def test_an_unbound_entry_raises_no_repair(self, hass: HomeAssistant) -> None:
        """Its setup fails with the reason; there is nothing else to say."""
        _entry(hass, "n1")

        async_sync_binding_issues(hass, await async_get_catalog(hass))

        assert _issue_ids(hass) == []

    async def test_a_product_that_moved_on_raises_one_repair_for_all_its_devices(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """One confirmation reaches every device on the product."""
        old = _candidate(doc=_doc(entity_name="Old"))
        first = _entry(hass, "n1")
        second = _entry(hass, "n2")
        await async_bind(hass, first, old)
        await async_bind(hass, second, old)
        (own_config_dir / CUSTOM_PROFILE_DIR).mkdir()
        (own_config_dir / CUSTOM_PROFILE_DIR / "plug.yaml").write_text(
            yaml.safe_dump(_doc(entity_name="New"))
        )
        catalog = await async_refresh_catalog(hass, online=False)

        async_sync_binding_issues(hass, catalog)

        issue_id = update_issue_id("acme/plug", SOURCE_LOCAL)
        assert _issue_ids(hass) == [issue_id]
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.is_fixable is True
        assert issue.translation_placeholders is not None
        assert issue.translation_placeholders["count"] == "2"
        assert issue.translation_placeholders["source"] == SOURCE_LOCAL
        current = next(
            c for c in catalog.by_id("acme/plug") if c.source == SOURCE_LOCAL
        )
        assert issue.data == {
            ISSUE_DATA_PRODUCT: "acme/plug",
            ISSUE_DATA_SOURCE: SOURCE_LOCAL,
            ISSUE_DATA_HASH: current.hash,
        }

    @staticmethod
    def _catalog_of(*candidates: ProductCandidate) -> ProfileCatalog:
        """Return a catalog offering exactly these candidates."""
        return ProfileCatalog(candidates={c.key: c for c in candidates})

    async def test_a_change_to_another_copy_of_the_product_is_not_offered(
        self, hass: HomeAssistant
    ) -> None:
        """A device bound from `local` is not moved by the shipped copy changing."""
        _entry(hass, "n1", ProfileBinding("acme/plug", SOURCE_LOCAL, "h-local"))
        catalog = self._catalog_of(
            ProductCandidate(
                "acme", "plug", "Mine", SOURCE_LOCAL, "h-local", location="plug.yaml"
            ),
            ProductCandidate("acme", "plug", "Shipped", SOURCE_BUILTIN, "h-builtin-2"),
        )

        async_sync_binding_issues(hass, catalog)

        assert _issue_ids(hass) == []

    async def test_two_sources_of_one_product_get_two_repairs(
        self, hass: HomeAssistant
    ) -> None:
        """Each device follows the copy it was bound from, and only that one."""
        from_local = _entry(
            hass, "n1", ProfileBinding("acme/plug", SOURCE_LOCAL, "old-local")
        )
        from_repo = _entry(
            hass, "n2", ProfileBinding("acme/plug", SOURCE_REPO, "old-repo")
        )
        catalog = self._catalog_of(
            ProductCandidate(
                "acme", "plug", "Mine", SOURCE_LOCAL, "new-local", location="plug.yaml"
            ),
            ProductCandidate(
                "acme", "plug", "Theirs", SOURCE_REPO, "new-repo", location="v/p.yaml"
            ),
        )

        async_sync_binding_issues(hass, catalog)

        local_issue = update_issue_id("acme/plug", SOURCE_LOCAL)
        repo_issue = update_issue_id("acme/plug", SOURCE_REPO)
        assert _issue_ids(hass) == sorted([local_issue, repo_issue])
        issues = ir.async_get(hass)
        for issue_id, source, new_hash, entry in (
            (local_issue, SOURCE_LOCAL, "new-local", from_local),
            (repo_issue, SOURCE_REPO, "new-repo", from_repo),
        ):
            issue = issues.async_get_issue(DOMAIN, issue_id)
            assert issue is not None
            assert issue.data == {
                ISSUE_DATA_PRODUCT: "acme/plug",
                ISSUE_DATA_SOURCE: source,
                ISSUE_DATA_HASH: new_hash,
            }
            assert issue.translation_placeholders is not None
            assert issue.translation_placeholders["count"] == "1"
            assert issue.translation_placeholders["devices"] == entry.title

    async def test_an_update_repair_is_withdrawn_once_nothing_is_outdated(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """Deleting the file, or adopting it, leaves nothing to offer."""
        old = _candidate(doc=_doc(entity_name="Old"))
        entry = _entry(hass, "n1")
        await async_bind(hass, entry, old)
        plug = own_config_dir / CUSTOM_PROFILE_DIR / "plug.yaml"
        plug.parent.mkdir()
        plug.write_text(yaml.safe_dump(_doc(entity_name="New")))
        async_sync_binding_issues(hass, await async_refresh_catalog(hass, online=False))
        assert _issue_ids(hass) == [update_issue_id("acme/plug", SOURCE_LOCAL)]

        plug.unlink()
        async_sync_binding_issues(hass, await async_refresh_catalog(hass, online=False))

        assert _issue_ids(hass) == []

    async def test_an_unbound_entry_is_not_the_sync_s_business(
        self, hass: HomeAssistant
    ) -> None:
        """Its repair is raised when it resolves, not here."""
        _entry(hass, "n1")
        async_sync_binding_issues(hass, await async_get_catalog(hass))
        assert _issue_ids(hass) == []


class TestProfileUpdateFlow:
    """The repair that adopts a product's newer content."""

    async def _outdated(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> tuple[MockConfigEntry, MockConfigEntry, ProductCandidate]:
        """Bind two devices to old content and put new content on disk."""
        old = _candidate(doc=_doc(entity_name="Old"))
        first = _entry(hass, "n1")
        second = _entry(hass, "n2")
        await async_bind(hass, first, old)
        await async_bind(hass, second, old)
        (own_config_dir / CUSTOM_PROFILE_DIR).mkdir()
        (own_config_dir / CUSTOM_PROFILE_DIR / "plug.yaml").write_text(
            yaml.safe_dump(_doc(entity_name="New"))
        )
        catalog = await async_refresh_catalog(hass, online=False)
        current = next(
            c for c in catalog.by_id("acme/plug") if c.source == SOURCE_LOCAL
        )
        return first, second, current

    async def _flow(
        self,
        hass: HomeAssistant,
        product_id: str,
        content_hash: str,
        source: str = SOURCE_LOCAL,
    ) -> ProfileUpdateFlow:
        flow = await async_create_fix_flow(
            hass,
            f"{ISSUE_UPDATE_PREFIX}x",
            {
                ISSUE_DATA_PRODUCT: product_id,
                ISSUE_DATA_SOURCE: source,
                ISSUE_DATA_HASH: content_hash,
            },
        )
        assert isinstance(flow, ProfileUpdateFlow)
        flow.hass = hass
        return flow

    async def test_the_form_shows_before_and_after_for_each_device(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """The user sees what changes before anything does."""
        first, second, current = await self._outdated(hass, own_config_dir)
        api = MagicMock()
        api.get_reported_config.return_value = (SWITCH_CONFIG, {})
        domain_data(hass).api = api
        flow = await self._flow(hass, "acme/plug", current.hash)

        result = await flow.async_step_init()

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_CONFIRM
        placeholders = result["description_placeholders"]
        assert placeholders is not None
        assert placeholders["count"] == "2"
        assert placeholders["product"] == "acme › Acme Plug-T1"
        assert placeholders["rows"] == (
            f"| {first.title} | 1 | 1 |\n| {second.title} | 1 | 1 |"
        )
        # Nothing has been applied yet.
        binding = ProfileBinding.from_entry(first)
        assert binding is not None
        assert binding.hash != current.hash

    async def test_counts_are_unknown_when_the_device_has_not_reported(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """A device not yet seen has nothing to count; the row says so."""
        first, _second, current = await self._outdated(hass, own_config_dir)
        api = MagicMock()
        api.get_reported_config.return_value = ({}, {})
        domain_data(hass).api = api
        flow = await self._flow(hass, "acme/plug", current.hash)

        result = await flow.async_step_init()

        placeholders = result["description_placeholders"]
        assert placeholders is not None
        assert placeholders["rows"].startswith(f"| {first.title} | ? | ? |")

    async def test_counts_are_unknown_without_an_api(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """Before any entry is set up there is no API to ask."""
        _first, _second, current = await self._outdated(hass, own_config_dir)
        flow = await self._flow(hass, "acme/plug", current.hash)

        result = await flow.async_step_init()

        placeholders = result["description_placeholders"]
        assert placeholders is not None
        assert "| ? | ? |" in placeholders["rows"]

    async def test_a_preview_that_blows_up_reads_as_unknown(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """A malformed report must not take the repair down with it."""
        _first, _second, current = await self._outdated(hass, own_config_dir)
        api = MagicMock()
        api.get_reported_config.return_value = (SWITCH_CONFIG, {})
        domain_data(hass).api = api
        flow = await self._flow(hass, "acme/plug", current.hash)

        with patch(
            "custom_components.esp_weaver.repairs.build_preview",
            side_effect=ValueError("malformed"),
        ):
            result = await flow.async_step_init()

        placeholders = result["description_placeholders"]
        assert placeholders is not None
        assert "| ? | ? |" in placeholders["rows"]

    async def test_confirming_rebinds_every_device_on_the_product(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """One confirmation, all devices, and the update repair goes."""
        first, second, current = await self._outdated(hass, own_config_dir)
        async_sync_binding_issues(hass, await async_get_catalog(hass))
        flow = await self._flow(hass, "acme/plug", current.hash)

        result = await flow.async_step_init({})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        for entry in (first, second):
            binding = ProfileBinding.from_entry(entry)
            assert binding is not None
            assert binding.hash == current.hash
        async_sync_binding_issues(hass, await async_get_catalog(hass))
        assert _issue_ids(hass) == []

    async def test_confirming_leaves_devices_bound_from_another_source_alone(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """The repair is for the copy it was raised about, not the product at large."""
        _first, _second, current = await self._outdated(hass, own_config_dir)
        other_binding = ProfileBinding("acme/plug", SOURCE_BUILTIN, "old-builtin")
        other = _entry(hass, "n3", other_binding)
        flow = await self._flow(hass, "acme/plug", current.hash)

        result = await flow.async_step_init({})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert ProfileBinding.from_entry(other) == other_binding

    async def test_the_issue_s_source_picks_the_copy(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """The same content from a source that does not offer it is gone."""
        _first, _second, current = await self._outdated(hass, own_config_dir)
        flow = await self._flow(hass, "acme/plug", current.hash, source=SOURCE_REPO)

        result = await flow.async_step_init()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_GONE

    async def test_a_product_that_moved_again_aborts(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """The issue is about content that is no longer what is on offer."""
        _first, _second, _current = await self._outdated(hass, own_config_dir)
        flow = await self._flow(hass, "acme/plug", "somethingelse0000")

        result = await flow.async_step_init()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_GONE

    async def test_a_product_that_vanished_aborts(self, hass: HomeAssistant) -> None:
        """Nothing to apply."""
        flow = await self._flow(hass, "acme/plug", "abc")
        result = await flow.async_step_init()
        assert result["reason"] == ABORT_GONE

    async def test_a_listing_that_cannot_be_fetched_aborts(
        self, hass: HomeAssistant, own_config_dir: Path
    ) -> None:
        """The repository went away between the issue and the fix."""
        _first, _second, current = await self._outdated(hass, own_config_dir)
        flow = await self._flow(hass, "acme/plug", current.hash)

        with patch(
            "custom_components.esp_weaver.repairs.async_fetch_content",
            AsyncMock(side_effect=CatalogError("offline")),
        ):
            result = await flow.async_step_init()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_FETCH_FAILED

    async def test_the_fix_flow_factory_copes_with_no_data(
        self, hass: HomeAssistant
    ) -> None:
        """An issue without data cannot be fixed, but must not crash the UI."""
        flow = await async_create_fix_flow(hass, "profile_update_x", None)
        assert isinstance(flow, ProfileUpdateFlow)
        flow.hass = hass
        result = await flow.async_step_init()
        assert result["reason"] == ABORT_GONE


class TestBindingChanged:
    """The update listener reloads an entry only when its binding changed."""

    async def test_a_changed_binding_reloads(self, hass: HomeAssistant) -> None:
        """A new product means new entities, which takes a reload."""
        entry = _entry(hass, "n1", ProfileBinding("acme/plug", SOURCE_LOCAL, "old"))
        domain_data(hass).loaded_bindings[entry.entry_id] = entry.options[
            OPTION_PROFILE
        ]
        hass.config_entries.async_update_entry(
            entry,
            options=options_with_binding(
                dict(entry.options), ProfileBinding("acme/plug", SOURCE_LOCAL, "new")
            ),
        )

        with patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ) as reload:
            await _async_binding_changed(hass, entry)

        reload.assert_awaited_once_with(entry.entry_id)

    async def test_a_rebind_prunes_the_content_left_behind(
        self, hass: HomeAssistant
    ) -> None:
        """What the entry was bound to before is pointed at by nothing now."""
        old = _candidate(doc=_doc(entity_name="Old"))
        new = _candidate(doc=_doc(entity_name="New"))
        entry = _entry(hass, "n1")
        await async_bind(hass, entry, old)
        store = await async_get_store(hass)
        _backdate(store, old)
        domain_data(hass).loaded_bindings[entry.entry_id] = entry.options[
            OPTION_PROFILE
        ]
        await async_bind(hass, entry, new)

        with patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ) as reload:
            await _async_binding_changed(hass, entry)

        reload.assert_awaited_once_with(entry.entry_id)
        assert store.get(old.id, old.hash) is None
        assert store.get(new.id, new.hash) is not None

    async def test_another_option_does_not_reload(self, hass: HomeAssistant) -> None:
        """Firmware on offer is written to options live and must not cost a reload."""
        binding = ProfileBinding("acme/plug", SOURCE_LOCAL, "same")
        entry = _entry(hass, "n1", binding)
        domain_data(hass).loaded_bindings[entry.entry_id] = entry.options[
            OPTION_PROFILE
        ]
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "available_firmware": "1.2.3"}
        )

        with patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ) as reload:
            await _async_binding_changed(hass, entry)

        reload.assert_not_awaited()


class TestDeviceIdentity:
    """What the device page says a device is, from the product it is bound to."""

    @staticmethod
    def _product(
        vendor: str, source: str = "local", model: str = "T1"
    ) -> ProductCandidate:
        return ProductCandidate.from_parsed(
            parse_profile_document(
                {
                    "version": 1,
                    "model": model,
                    "vendor": vendor,
                    **(
                        {
                            "vendor_label": "Espressif",
                            "vendor_labels": {"zh-Hans": "乐鑫"},
                        }
                        if vendor == "espressif"
                        else {}
                    ),
                    "product": "bulb",
                    "product_label": "Bulb",
                },
                f"{vendor}/bulb",
            ),
            source=source,
        )

    def test_the_vendor_is_the_manufacturer_and_the_label_the_model(self) -> None:
        """The two fields Home Assistant shows under the name, and filters by."""
        identity = DeviceIdentity.of("node1", self._product("acme"))

        assert identity == DeviceIdentity(
            serial_number="node1", manufacturer="acme", model="Bulb"
        )

    def test_the_manufacturer_uses_the_localized_vendor_name(self) -> None:
        """The device page follows Home Assistant's language."""
        product = ProductCandidate.from_parsed(
            parse_profile_document(
                {
                    "version": 1,
                    "model": "T1",
                    "vendor": "acme",
                    "vendor_label": "Acme",
                    "vendor_labels": {"zh-Hans": "阿克米"},
                    "product": "bulb",
                    "product_label": "Bulb",
                },
                "acme/bulb",
            ),
            source="local",
        )

        assert DeviceIdentity.of("node1", product).manufacturer == "Acme"
        assert DeviceIdentity.of("node1", product, "zh-Hans").manufacturer == "阿克米"

    def test_the_product_s_model_is_not_the_page_s_model_id(self) -> None:
        """The model id is the device's own word, read from its config, not the product's."""
        identity = DeviceIdentity.of("node1", self._product("acme"))

        assert not hasattr(identity, "model_id")

    def test_a_built_in_product_names_the_vendor(self) -> None:
        """The vendor picked in the flow is the manufacturer, built-in included."""
        identity = DeviceIdentity.of(
            "node1", self._product("espressif", "builtin", "*")
        )

        assert identity.manufacturer == "Espressif"
        assert identity.model == "Bulb"

    def test_a_kept_copy_of_a_built_in_product_names_the_vendor_too(self) -> None:
        """Setup reads the store, where every product is "cached"; the vendor still counts."""
        kept = replace(
            self._product("espressif", "builtin"),
            source=SOURCE_CACHED,
            origin="builtin",
        )

        identity = DeviceIdentity.of("node1", kept)

        assert identity.manufacturer == "Espressif"
        assert identity.model == "Bulb"

    def test_a_vendor_s_own_file_names_the_vendor(self) -> None:
        """A file in the configuration directory is the vendor's, whatever it is called."""
        identity = DeviceIdentity.of("node1", self._product("local"))

        assert identity.manufacturer == "local"
        assert identity.model == "Bulb"

    def test_no_product_gives_only_the_serial_number(self) -> None:
        """Nothing is known about it beyond the node id."""
        identity = DeviceIdentity.of("node1", None)

        assert identity == DeviceIdentity("node1")
