# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reading the repository from a pull request on GitHub, by the index its CI built."""

import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import yaml

from custom_components.esp_weaver.profiles.catalog import async_refresh_catalog
from custom_components.esp_weaver.profiles.github import (
    GITHUB_API,
    GITHUB_RAW,
    INDEX_BRANCH,
    UPSTREAM_REPO,
    RefError,
    RepositoryRef,
    async_read_ref,
)
from custom_components.esp_weaver.profiles.index import (
    IndexSource,
    index_document,
    index_products,
    is_profile_path,
    parse_index,
    render_index,
)
from custom_components.esp_weaver.profiles.mirror import (
    KEY_SOURCE,
    KEY_SOURCE_COMMIT,
    STORAGE_KEY,
    RepositoryMirror,
    async_get_mirror,
)
from custom_components.esp_weaver.profiles.product import SOURCE_REPO
from custom_components.esp_weaver.profiles.schema import ProfileError, content_hash
from tests.github_fake import (
    COMMIT,
    FORK,
    product_doc,
    product_file,
    pull_index,
    serve_ref,
)


class TestProfilePaths:
    """Which paths in the repository are product files."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("vendors/acme/plug.yaml", True),
            ("vendors/acme/plug.yml", True),
            ("vendors/.gitkeep", False),
            ("vendors/acme/.plug.yaml.swp", False),
            ("vendors/acme/README.md", False),
            ("vendors/acme/deeper/plug.yaml", False),
            ("index.json", False),
            ("README.md", False),
        ],
    )
    def test_only_a_vendor_s_yaml_counts(self, path: str, expected: bool) -> None:
        """A product is a YAML file one level under a vendor, and not a dotfile."""
        assert is_profile_path(path) is expected


class TestIndexing:
    """The rules a repository's files have to meet, shared by the tool and GitHub."""

    def test_each_file_becomes_a_product_with_its_content(self) -> None:
        """The index lists what the list needs, and the candidate holds the file."""
        plug = product_doc(
            labels={"zh-Hans": "Acme 插头"},
            vendor_label="Acme",
            vendor_labels={"zh-Hans": "阿克米"},
        )
        other = product_doc(product="other", label="Acme Other")

        [first, second] = index_products([product_file(other), product_file(plug)])

        assert (first.id, second.id) == ("acme/other", "acme/plug")
        assert first.source == SOURCE_REPO
        assert first.location == "vendors/acme/other.yaml"
        assert first.content is not None
        assert first.hash == content_hash(other)
        document = index_document([first, second])
        assert document["version"] == 1
        assert document["products"][1] == {
            "vendor": "acme",
            "product": "plug",
            "product_label": "Acme Plug",
            "product_labels": {"zh-Hans": "Acme 插头"},
            "vendor_label": "Acme",
            "vendor_labels": {"zh-Hans": "阿克米"},
            "model": "T1",
            "category": second.category,
            "path": "vendors/acme/plug.yaml",
            "hash": second.hash,
        }
        assert render_index(document).endswith("}\n")

    def test_a_file_outside_the_layout_is_refused(self) -> None:
        """Only vendors/<vendor>/<product>.yaml is a product."""
        with pytest.raises(ProfileError, match="expected vendors/<vendor>"):
            index_products([("vendors/acme/deeper/plug.yaml", "")])

    def test_the_file_has_to_agree_with_its_path(self) -> None:
        """The path is the identity the repository is organised by."""
        with pytest.raises(ProfileError, match="declares acme/plug but sits under"):
            index_products(
                [("vendors/acme/socket.yaml", yaml.safe_dump(product_doc()))]
            )

    def test_an_invalid_file_is_refused_with_the_schema_s_words(self) -> None:
        """What the integration would refuse, the index refuses."""
        with pytest.raises(ProfileError):
            index_products([("vendors/acme/plug.yaml", "vendor: acme\n")])

    @pytest.mark.parametrize("key", ["product_label", "model", "vendor_label"])
    def test_blanks_at_an_end_are_refused(self, key: str) -> None:
        """Invisible in the list, but a model is compared byte for byte."""
        doc = product_doc()
        doc[key] = " padded "

        with pytest.raises(ProfileError, match=f"{key} ' padded ' starts or ends"):
            index_products([product_file(doc)])

    def test_blanks_in_a_localized_label_are_refused(self) -> None:
        """Localized labels follow the same visible-name rules as the default."""
        doc = product_doc(labels={"zh-Hans": " 有空格 "})

        with pytest.raises(
            ProfileError, match=r"product_labels\.zh-Hans .* starts or ends"
        ):
            index_products([product_file(doc)])

        doc = product_doc(vendor_labels={"zh-Hans": " 有空格 "})
        with pytest.raises(
            ProfileError, match=r"vendor_labels\.zh-Hans .* starts or ends"
        ):
            index_products([product_file(doc)])

    def test_two_files_showing_the_same_name_are_refused(self) -> None:
        """The list is where products are told apart."""
        files = [
            product_file(product_doc()),
            product_file(product_doc(product="plug2", label="Acme  Plug")),
        ]

        with pytest.raises(ProfileError, match="both show as 'Acme Plug-T1'"):
            index_products(files)

    def test_two_files_with_one_id_are_refused(self) -> None:
        """plug.yaml and plug.yml are the same product twice."""
        files = [
            product_file(product_doc()),
            ("vendors/acme/plug.yml", yaml.safe_dump(product_doc(label="Other name"))),
        ]

        with pytest.raises(ProfileError, match="same product id"):
            index_products(files)


class TestTheIndexDocument:
    """What the tool writes and the integration reads back."""

    def test_the_published_index_locates_files_by_the_base_it_came_from(
        self,
    ) -> None:
        """No source in the document: the files sit where the index was read."""
        text = render_index(
            index_document(
                index_products(
                    [
                        product_file(
                            product_doc(
                                labels={"zh-Hans": "Acme 插头"},
                                vendor_label="Acme",
                                vendor_labels={"zh-Hans": "阿克米"},
                            )
                        )
                    ]
                )
            )
        )

        parsed = parse_index(text, "https://example.com/profiles/")

        assert parsed.source is None
        assert parsed.error is None
        [plug] = parsed.products
        assert plug.url == "https://example.com/profiles/vendors/acme/plug.yaml"
        assert plug.content is None
        assert plug.labels == {"zh-Hans": "Acme 插头"}
        assert plug.localized_vendor("zh-Hans") == "阿克米"

    def test_a_pull_request_s_index_locates_files_by_commit(self) -> None:
        """The source names the fork and commit; the base given is ignored."""
        parsed = parse_index(pull_index(product_doc()), "https://example.com/")

        assert parsed.source == IndexSource(123, FORK, COMMIT)
        [plug] = parsed.products
        assert plug.url == f"{GITHUB_RAW}/{FORK}/{COMMIT}/vendors/acme/plug.yaml"

    def test_a_refusal_travels_in_the_index(self) -> None:
        """CI's words reach the integration in place of products."""
        parsed = parse_index(pull_index(error="plug.yaml: bad"), None)

        assert parsed.error == "plug.yaml: bad"
        assert parsed.products == []

    def test_an_index_that_says_nothing_about_its_files_is_refused(self) -> None:
        """No base and no source: nothing could be downloaded from it."""
        text = render_index(index_document([]))

        with pytest.raises(ProfileError, match="where its files are"):
            parse_index(text, None)

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            '{"version": 2, "products": []}',
            '{"version": 1}',
            '{"version": 1, "products": [], "source": {"pull": 0, "repo": "a/b", "commit": "'
            + COMMIT
            + '"}}',
            '{"version": 1, "products": [], "source": {"pull": 1, "repo": "a/b", "commit": "abc"}}',
            '{"version": 1, "products": [], "source": {"pull": 1, "repo": "nope", "commit": "'
            + COMMIT
            + '"}}',
        ],
    )
    def test_what_is_not_an_index_is_refused(self, text: str) -> None:
        """The schema is the contract with the tool."""
        with pytest.raises(ProfileError, match="not usable as an index"):
            parse_index(text, "https://example.com/")


class TestParsingARef:
    """What the user may type or paste."""

    @pytest.mark.parametrize(
        "typed",
        [
            "123",
            "#123",
            " 123 ",
            "https://github.com/espressif/esp-weaver-profiles/pull/123",
            "https://github.com/espressif/esp-weaver-profiles/pull/123/files",
            "http://www.github.com/espressif/esp-weaver-profiles/pull/123",
            "github.com/espressif/esp-weaver-profiles/pull/123",
        ],
    )
    def test_a_number_or_the_page_url_is_read(self, typed: str) -> None:
        """Every form is the same pull request, kept as its short form."""
        ref = RepositoryRef.parse(typed)

        assert ref == RepositoryRef(pull=123)
        assert ref.spec == "#123"
        assert RepositoryRef.parse(ref.spec) == ref

    @pytest.mark.parametrize(
        "typed",
        [
            "",
            "0",
            "#0",
            "plug",
            "12a",
            "acme:add-plug",
            "https://github.com/acme/esp-weaver-profiles/pull/5",
            "https://github.com/espressif/esp-weaver-profiles/tree/add-plug",
            "https://github.com/espressif/esp-weaver-profiles",
            "https://github.com/espressif/esp_weaver/pull/123",
            "https://example.com/espressif/esp-weaver-profiles/pull/123",
        ],
    )
    def test_anything_else_is_refused(self, typed: str) -> None:
        """Only the upstream's CI publishes indexes, so only its pull requests read."""
        with pytest.raises(ValueError):
            RepositoryRef.parse(typed)

    def test_where_the_index_is_read_from(self) -> None:
        """One file per pull request on the index branch, by API or raw."""
        ref = RepositoryRef(pull=123)

        assert ref.index_path == "pr-123/index.json"
        assert ref.api_url == (
            f"{GITHUB_API}/repos/{UPSTREAM_REPO}/contents/pr-123/index.json"
            f"?ref={INDEX_BRANCH}"
        )
        assert ref.raw_url == (
            f"{GITHUB_RAW}/{UPSTREAM_REPO}/{INDEX_BRANCH}/pr-123/index.json"
        )


class TestReadingARef:
    """One call for the index CI published; the files wait to be picked."""

    async def test_the_index_is_read_through_the_api(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Fresh on every read: the API is not cached. Nothing else is fetched."""
        serve_ref(
            aioclient_mock, product_doc(), product_doc(product="other", label="Other")
        )

        contents = await async_read_ref(hass, RepositoryRef(pull=123))

        assert contents.commit == COMMIT
        assert contents.repo == FORK
        assert [c.id for c in contents.products] == ["acme/other", "acme/plug"]
        plug = contents.products[1]
        assert plug.content is None
        assert plug.url == f"{GITHUB_RAW}/{FORK}/{COMMIT}/vendors/acme/plug.yaml"
        assert plug.location == "vendors/acme/plug.yaml"
        assert aioclient_mock.call_count == 1
        assert (
            aioclient_mock.mock_calls[0][3]["Accept"]
            == "application/vnd.github.raw+json"
        )

    async def test_the_allowance_spent_falls_back_to_the_raw_file(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Sixty calls an hour; past that the raw file serves, a little stale maybe."""
        serve_ref(aioclient_mock, product_doc(), quota_spent=True)

        contents = await async_read_ref(hass, RepositoryRef(pull=123))

        assert [c.id for c in contents.products] == ["acme/plug"]
        assert aioclient_mock.call_count == 2

    async def test_a_pull_request_with_no_index_yet(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A wrong number, or checks still running: said so, not a network trouble."""
        serve_ref(aioclient_mock, published=False)

        with pytest.raises(RefError, match="has no index on GitHub yet"):
            await async_read_ref(hass, RepositoryRef(pull=123))

    async def test_no_index_when_the_allowance_is_spent_either(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The raw fallback's 404 is the same answer."""
        ref = RepositoryRef(pull=123)
        aioclient_mock.get(ref.api_url, status=429)
        aioclient_mock.get(ref.raw_url, status=404)

        with pytest.raises(RefError, match="has no index on GitHub yet"):
            await async_read_ref(hass, ref)

    async def test_another_failure_is_reported_as_it_is(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A 500 is not a missing index."""
        aioclient_mock.get(RepositoryRef(pull=123).api_url, status=500)

        with pytest.raises(RefError, match="#123: HTTP 500"):
            await async_read_ref(hass, RepositoryRef(pull=123))

    async def test_a_pull_request_with_no_products_is_empty_not_an_error(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A pull request that removed the last file lists nothing."""
        serve_ref(aioclient_mock)

        contents = await async_read_ref(hass, RepositoryRef(pull=123))

        assert contents.products == []

    async def test_files_ci_refused_are_refused_here_in_ci_s_words(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The vendor sees the same refusal in the form as the pull request shows."""
        serve_ref(
            aioclient_mock,
            error="vendors/acme/plug.yaml: label ' Padded' starts or ends with whitespace",
        )

        with pytest.raises(
            RefError, match="refused by its checks.*whitespace"
        ) as raised:
            await async_read_ref(hass, RepositoryRef(pull=123))
        assert COMMIT[:7] in str(raised.value)

    async def test_an_index_of_another_pull_request_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The file has to be about the pull request asked for."""
        serve_ref(aioclient_mock, index=pull_index(product_doc(), pull=7))

        with pytest.raises(RefError, match="of another source"):
            await async_read_ref(hass, RepositoryRef(pull=123))

    async def test_the_published_index_format_without_a_source_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Without a commit there is no telling where the files are."""
        serve_ref(aioclient_mock, index=render_index(index_document([])))

        with pytest.raises(RefError, match="where its files are"):
            await async_read_ref(hass, RepositoryRef(pull=123))

    async def test_what_is_not_an_index_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Whatever GitHub answers, it has to be an index."""
        serve_ref(aioclient_mock, index="<html>")

        with pytest.raises(RefError, match="not usable as an index"):
            await async_read_ref(hass, RepositoryRef(pull=123))


class TestFollowingARef:
    """The repository copy reading a pull request instead of the index."""

    @pytest.fixture(autouse=True)
    def _config_dir(self, own_config_dir: Path) -> None:
        """Write profiles somewhere this test owns."""

    async def test_the_copy_lists_the_pull_request_and_says_so(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        hass_storage: dict[str, Any],
    ) -> None:
        """The listing is the pull request's index; the files wait to be picked."""
        serve_ref(aioclient_mock, product_doc())
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))

        catalog = await async_refresh_catalog(hass, online=True)

        assert catalog.index_error == ""
        assert catalog.repository_source == "#123"
        assert catalog.repository_commit == COMMIT
        [plug] = catalog.by_id("acme/plug")
        assert plug.source == SOURCE_REPO
        assert plug.content is None
        assert plug.key == "repo:vendors/acme/plug.yaml"
        data = hass_storage[STORAGE_KEY]["data"]
        assert data[KEY_SOURCE] == "#123"
        assert data[KEY_SOURCE_COMMIT] == COMMIT
        assert data["products"]["acme/plug"]["content"] is None
        assert aioclient_mock.call_count == 1

    async def test_a_picked_product_is_downloaded_from_the_commit(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """One file, by the commit the index was built from, checked against it."""
        serve_ref(aioclient_mock, product_doc())
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))
        catalog = await async_refresh_catalog(hass, online=True)
        [plug] = catalog.by_id("acme/plug")

        fetched = await mirror.async_fetch(hass, plug)

        assert fetched.content is not None
        assert fetched.hash == plug.hash
        assert aioclient_mock.mock_calls[-1][1].path == (
            f"/{FORK}/{COMMIT}/vendors/acme/plug.yaml"
        )

    async def test_a_new_commit_moves_a_held_product_to_the_new_file(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A push re-indexes; a product this household holds is downloaded again."""
        serve_ref(aioclient_mock, product_doc())
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))
        catalog = await async_refresh_catalog(hass, online=True)
        [plug] = catalog.by_id("acme/plug")
        await mirror.async_fetch(hass, plug)
        aioclient_mock.clear_requests()
        later = "f" * 40
        serve_ref(aioclient_mock, product_doc(model="T2"), commit=later)

        catalog = await async_refresh_catalog(hass, online=True)

        assert catalog.repository_commit == later
        [plug] = catalog.by_id("acme/plug")
        assert plug.model == "T2"
        assert plug.content is not None

    async def test_what_is_followed_survives_a_restart(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Read back from the store by a fresh copy, as after a restart."""
        serve_ref(aioclient_mock, product_doc())
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))
        await async_refresh_catalog(hass, online=True)

        fresh = RepositoryMirror(hass)
        await fresh.async_load()

        assert fresh.source == "#123"
        assert fresh.source_commit == COMMIT
        assert fresh.ref == RepositoryRef(pull=123)

    async def test_a_pull_request_that_cannot_be_read_leaves_the_copy_as_it_was(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The failure is the catalog's index error; what was listed stays."""
        serve_ref(aioclient_mock, product_doc())
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))
        await async_refresh_catalog(hass, online=True)
        aioclient_mock.clear_requests()
        serve_ref(aioclient_mock, published=False)

        catalog = await async_refresh_catalog(hass, online=True)

        assert "no index on GitHub yet" in catalog.index_error
        assert catalog.repository_commit == COMMIT
        assert [c.id for c in catalog.repository()] == ["acme/plug"]

    async def test_following_the_index_again_forgets_the_commit(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Clearing goes back to the published index on the next refresh."""
        serve_ref(aioclient_mock, product_doc())
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))
        await async_refresh_catalog(hass, online=True)

        await mirror.async_follow(None)
        catalog = await async_refresh_catalog(hass, online=True)

        assert mirror.source is None
        assert mirror.source_commit is None
        assert catalog.repository_source is None
        # The index is offline in tests, so the copy keeps what it listed and
        # says the refresh fell short.
        assert catalog.index_error
        assert [c.id for c in catalog.repository()] == ["acme/plug"]

    async def test_following_what_is_followed_already_writes_nothing(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Typing the same thing again is not a change."""
        mirror = await async_get_mirror(hass)
        await mirror.async_follow(RepositoryRef.parse("123"))
        written = dict(hass_storage[STORAGE_KEY])

        await mirror.async_follow(RepositoryRef.parse("#123"))

        assert hass_storage[STORAGE_KEY] == written

    async def test_a_stored_source_this_version_cannot_read_is_dropped(
        self,
        hass: HomeAssistant,
        hass_storage: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The copy follows the index again, and says so once."""
        hass_storage[STORAGE_KEY] = {
            "version": 1,
            "key": STORAGE_KEY,
            "data": {
                "products": {},
                KEY_SOURCE: "acme:add-plug",
                KEY_SOURCE_COMMIT: COMMIT,
            },
        }
        caplog.set_level(logging.WARNING)

        mirror = await async_get_mirror(hass)

        assert mirror.source is None
        assert mirror.source_commit is None
        assert "follows the index again" in caplog.text
