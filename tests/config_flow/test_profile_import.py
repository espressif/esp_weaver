# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test getting a device profile into the vendor directory.

Two ways in: a URL the user pastes, and a file they upload through the config
flow. Both end at the same validate-then-write, so the refusals are exercised
once against the URL path and the upload tests cover what only it can get wrong.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
from random import getrandbits
import tempfile
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import FormData
from aiohttp.test_utils import TestClient
from homeassistant.components import file_upload
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util.ulid import ulid_hex
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
import yaml

from custom_components.esp_weaver.flows.const import (
    ABORT_NO_DEVICES,
    ERROR_PROFILE_IMPORT_FAILED,
    FIELD_ACTION,
    FIELD_OVERWRITE,
    FIELD_PROFILE,
    FIELD_PROFILE_FILE,
    FIELD_REPOSITORY_SOURCE,
    FIELD_VENDOR,
    NAV_ACTION_BACK,
    PROFILE_IMPORT_ERRORS,
    STEP_PROFILE_SELECT,
    STEP_PROFILE_VENDOR,
)
from custom_components.esp_weaver.identity import DOMAIN
from custom_components.esp_weaver.profiles.importer import (
    MAX_PROFILE_SIZE,
    async_import_profile,
    async_save_profile,
    raw_url,
)
from custom_components.esp_weaver.profiles.loader import CUSTOM_PROFILE_DIR, scan_local
from custom_components.esp_weaver.profiles.mirror import async_get_mirror
from tests.github_fake import product_doc, serve_ref

URL = "https://example.com/profiles/acme_valve.yaml"

# Making a directory unwritable is how the two failure tests below arrange a
# failure, and root writes to it anyway. Running the suite as root is common
# enough in containers that the alternative - two tests that fail for no reason
# anyone can act on - would just teach people to ignore a red suite.
needs_unprivileged = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the directory permissions these tests rely on",
)

PROFILE: dict[str, Any] = {
    "version": 1,
    "model": "T1",
    "vendor": "acme",
    "product": "valve",
    "devices": [
        {
            "type": "esp.device.aroma",
            "entities": [
                {
                    "platform": "switch",
                    "name": "Aroma Diffuser",
                    "key": "aroma",
                    "attributes": {
                        "state": {
                            "param": "Power",
                            "type": "esp.param.power",
                            "data_type": "bool",
                            "properties": ["read", "write"],
                        }
                    },
                }
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _config_dir(own_config_dir: Path) -> None:
    """Write profiles somewhere this test owns, not the shared config."""


def _body() -> str:
    """Return a valid profile as it would be served."""
    return yaml.safe_dump(PROFILE)


class TestRawUrl:
    """A pasted link is usually the page, not the file."""

    def test_a_github_file_page_becomes_the_raw_file(self) -> None:
        """That page serves HTML, and it is the first mistake everyone makes."""
        assert (
            raw_url("https://github.com/acme/profiles/blob/main/valve.yaml")
            == "https://raw.githubusercontent.com/acme/profiles/main/valve.yaml"
        )

    def test_a_gist_gets_the_raw_suffix(self) -> None:
        """Gists are the quickest way to hand someone a profile."""
        assert (
            raw_url("https://gist.github.com/someone/2f9a1b3c4d5e6f7a8b9c")
            == "https://gist.github.com/someone/2f9a1b3c4d5e6f7a8b9c/raw"
        )

    def test_a_raw_link_is_left_alone(self) -> None:
        """Rewriting one twice would break it."""
        url = "https://raw.githubusercontent.com/acme/profiles/main/valve.yaml"
        assert raw_url(url) == url

    def test_any_other_host_is_left_alone(self) -> None:
        """A vendor serving the file themselves is the point of taking a URL."""
        assert raw_url(URL) == URL


class TestImport:
    """The happy path lands a file in the vendor directory."""

    async def test_it_writes_the_file_and_says_where(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The path is what the caller needs to find it again."""
        aioclient_mock.get(URL, text=_body())

        path = await async_import_profile(hass, URL)

        assert path == Path(hass.config.path(CUSTOM_PROFILE_DIR, "acme_valve.yaml"))
        assert await hass.async_add_executor_job(path.read_text) == _body()

    async def test_the_imported_profile_is_the_one_that_loads(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Landing in the directory is only useful if the loader reads it."""
        aioclient_mock.get(URL, text=_body())
        await async_import_profile(hass, URL)

        products, _ = await hass.async_add_executor_job(
            scan_local, Path(hass.config.path(CUSTOM_PROFILE_DIR)), "local"
        )

        assert [p.id for p in products] == ["acme/valve"]

    async def test_the_directory_is_created_if_missing(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Importing before any device is added is a reasonable first move."""
        aioclient_mock.get(URL, text=_body())

        path = await async_import_profile(hass, URL)

        assert path.parent.is_dir()

    async def test_the_filename_can_be_given(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Links that end in something unhelpful still have to be importable."""
        aioclient_mock.get(URL, text=_body())

        path = await async_import_profile(hass, URL, "mine.yaml")

        assert path.name == "mine.yaml"

    async def test_a_gist_is_named_after_the_gist(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A gist's raw URL ends in /raw, which names nothing at all.

        The service's own description offers gist links, so refusing one unless
        a filename is also filled in would be a trap.
        """
        gist = "https://gist.github.com/someone/2f9a1b3c4d5e6f7a8b9c"
        aioclient_mock.get(f"{gist}/raw", text=_body())

        path = await async_import_profile(hass, gist)

        assert path.name == "gist_2f9a1b3c4d5e6f7a8b9c.yaml"


class TestImportRefusals:
    """Nothing lands unless it is a profile, so a bad link changes nothing."""

    @staticmethod
    def _files(hass: HomeAssistant) -> list[str]:
        """Return what is in the vendor directory."""
        directory = Path(hass.config.path(CUSTOM_PROFILE_DIR))
        return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []

    async def test_a_download_failure_is_reported(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A typo in the link is the likeliest failure of all."""
        aioclient_mock.get(URL, status=404)

        with pytest.raises(ServiceValidationError, match="import_download_failed"):
            await async_import_profile(hass, URL)

        assert self._files(hass) == []

    async def test_an_html_page_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Pasting a file page rather than the raw link has to say so."""
        aioclient_mock.get(URL, text="<html><body>not a profile</body></html>")

        with pytest.raises(ServiceValidationError, match="import_invalid"):
            await async_import_profile(hass, URL)

        assert self._files(hass) == []

    async def test_broken_yaml_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The parser error is the only useful thing we can say here."""
        aioclient_mock.get(
            URL, text="version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: ["
        )

        with pytest.raises(ServiceValidationError):
            await async_import_profile(hass, URL)

        assert self._files(hass) == []

    async def test_a_profile_that_fails_validation_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Writing it anyway would raise a repair on every load until deleted."""
        aioclient_mock.get(
            URL,
            text="version: 1\nvendor: acme\nproduct: thing\nmodel: T1\ndevices: [{nope: 1}]\n",
        )

        with pytest.raises(ServiceValidationError, match="import_invalid"):
            await async_import_profile(hass, URL)

        assert self._files(hass) == []

    async def test_something_far_too_large_is_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A link pointing at a disk image should not be read into memory."""
        aioclient_mock.get(URL, text="#" * (MAX_PROFILE_SIZE + 10))

        with pytest.raises(ServiceValidationError, match="import_too_large"):
            await async_import_profile(hass, URL)

        assert self._files(hass) == []

    async def test_an_existing_file_is_kept(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Overwriting someone's edited profile silently would be unforgivable."""
        aioclient_mock.get(URL, text=_body())
        path = await async_import_profile(hass, URL)
        await hass.async_add_executor_job(
            path.write_text, "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\n"
        )

        with pytest.raises(ServiceValidationError, match="import_exists"):
            await async_import_profile(hass, URL)

        assert (
            await hass.async_add_executor_job(path.read_text)
            == "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\n"
        )

    async def test_overwrite_replaces_it(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Re-importing after fixing the source is the normal update path."""
        aioclient_mock.get(URL, text=_body())
        path = await async_import_profile(hass, URL)
        await hass.async_add_executor_job(
            path.write_text, "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\n"
        )

        await async_import_profile(hass, URL, overwrite=True)

        assert await hass.async_add_executor_job(path.read_text) == _body()

    @pytest.mark.parametrize(
        "filename",
        [
            "../escape.yaml",
            "/etc/escape.yaml",
            "sub/dir.yaml",
            "back\\slash.yaml",
            "notes.txt",
            ".yaml",
            # Hidden, and it would collide with the temporary file _place
            # writes beside its target.
            ".acme.yaml",
            "~acme.yaml",
            "line\nbreak.yaml",
        ],
    )
    async def test_names_that_are_not_plain_filenames_are_refused(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        filename: str,
    ) -> None:
        """The name becomes a path under the configuration directory."""
        aioclient_mock.get(URL, text=_body())

        with pytest.raises(ServiceValidationError, match="import_bad_filename"):
            await async_import_profile(hass, URL, filename)

        assert self._files(hass) == []

    @pytest.mark.parametrize(
        "filename",
        [
            # What every browser calls the second copy of a download.
            "acme (1).yaml",
            "灯泡配置.yaml",
            "ACME Profile v2.yml",
            "_acme.yaml",
            "-acme.yaml",
            "backup~.yaml",
        ],
    )
    async def test_names_a_filesystem_accepts_are_kept(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        filename: str,
    ) -> None:
        """Refusing these would refuse names people actually have.

        Spaces and a bracketed number are what a browser produces on its own,
        and a profile written for someone's own device need not be named in
        English.
        """
        aioclient_mock.get(URL, text=_body())

        path = await async_import_profile(hass, URL, filename)

        assert path.name == filename
        assert self._files(hass) == [filename]

    async def test_a_link_to_a_name_with_a_space_keeps_the_space(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A percent-escaped name saved literally would be unrecognisable."""
        url = "https://example.com/profiles/acme%20valve.yaml"
        aioclient_mock.get(url, text=_body())

        path = await async_import_profile(hass, url)

        assert path.name == "acme valve.yaml"

    async def test_an_escaped_separator_in_a_link_is_still_refused(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Decoding a name is only safe because the result is checked after."""
        url = "https://example.com/p/%2E%2E%2F%2E%2E%2Fescape.yaml"
        aioclient_mock.get(url, text=_body())

        with pytest.raises(ServiceValidationError, match="import_bad_filename"):
            await async_import_profile(hass, url)

        assert self._files(hass) == []

    async def test_an_empty_filename_means_unset(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A text field left blank in the UI arrives as an empty string."""
        aioclient_mock.get(URL, text=_body())

        path = await async_import_profile(hass, URL, "")

        assert path.name == "acme_valve.yaml"

    async def test_a_url_without_a_filename_asks_for_one(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Guessing a name would hide that the link is probably wrong."""
        with pytest.raises(ServiceValidationError, match="import_bad_filename"):
            await async_import_profile(hass, "https://example.com/profiles/")

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://example.com/x.yaml", "//example.com/x"]
    )
    async def test_a_download_that_ends_somewhere_other_than_the_web_is_refused(
        self, hass: HomeAssistant, url: str
    ) -> None:
        """Redirects are followed, so where one ends is not where it was aimed.

        Gist links depend on being followed elsewhere, so refusing to follow is
        not on the table; what the scheme rules out is a hop that turns the
        download into a read of whatever Home Assistant itself can see.
        """
        with pytest.raises(ServiceValidationError, match="import_download_failed"):
            await async_import_profile(hass, url, filename="x.yaml")

    async def test_a_dropped_connection_is_not_blamed_on_the_user(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A link that is right but unreachable is a device error, not input."""
        aioclient_mock.get(URL, exc=TimeoutError())

        with pytest.raises(HomeAssistantError, match="import_download_failed") as err:
            await async_import_profile(hass, URL)

        assert not isinstance(err.value, ServiceValidationError)

    @needs_unprivileged
    async def test_a_failed_write_leaves_the_previous_profile_alone(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A half-written profile costs every device type it declared.

        The user this exists for has no shell, so they could not delete the
        remains either.
        """
        aioclient_mock.get(URL, text=_body())
        path = await async_import_profile(hass, URL)
        await hass.async_add_executor_job(
            path.write_text, "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\n"
        )
        await hass.async_add_executor_job(path.parent.chmod, 0o500)

        try:
            with pytest.raises(HomeAssistantError, match="import_write_failed"):
                await async_import_profile(hass, URL, overwrite=True)
        finally:
            await hass.async_add_executor_job(path.parent.chmod, 0o700)

        assert (
            await hass.async_add_executor_job(path.read_text)
            == "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\n"
        )
        assert self._files(hass) == ["acme_valve.yaml"]

    @needs_unprivileged
    async def test_an_unwritable_directory_is_reported(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A read-only configuration directory is not ours to fix."""
        aioclient_mock.get(URL, text=_body())
        directory = Path(hass.config.path(CUSTOM_PROFILE_DIR))
        await hass.async_add_executor_job(directory.mkdir)
        await hass.async_add_executor_job(directory.chmod, 0o500)

        try:
            with pytest.raises(HomeAssistantError, match="import_write_failed"):
                await async_import_profile(hass, URL)
        finally:
            await hass.async_add_executor_job(directory.chmod, 0o700)


@dataclass(frozen=True)
class Browser:
    """The part of a browser the upload step relies on.

    The file selector posts the chosen file to file_upload and hands the flow
    the id it gets back, so that is what this does: the flow then reads a file
    the real component put on disk, under a name that went through its checks.
    """

    client: TestClient
    temp_dir: Path

    async def upload(self, name: str, content: str | bytes) -> str:
        """Post one file as the browser would, and return its id."""
        form = FormData(quote_fields=False)
        form.add_field(
            "file",
            content.encode("utf-8") if isinstance(content, str) else content,
            filename=name,
        )

        response = await self.client.post("/api/file_upload", data=form)

        assert response.status == 200
        return (await response.json())["file_id"]

    def directory(self, file_id: str) -> Path:
        """Return where file_upload keeps that upload until it is consumed."""
        return self.temp_dir / file_id


@pytest.fixture
async def browser(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> AsyncIterator[Browser]:
    """Install the integration and return a way to post files to it.

    The integration comes first, since the test client freezes the HTTP
    router; the upload directory is varied per test for parallel runs.
    """
    assert await async_setup_component(hass, DOMAIN, {})
    name = f"{file_upload.TEMP_DIR_NAME}-{getrandbits(20):05x}"

    with patch("homeassistant.components.file_upload.TEMP_DIR_NAME", name):
        yield Browser(await hass_client(), Path(tempfile.gettempdir()) / name)


async def _import(hass: HomeAssistant, fields: dict[str, object]) -> Any:
    """Submit the fields to the profile step, which the flow opens on.

    Typed loosely, as every test indexes into the result and the placeholders
    it holds are optional by declaration.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"

    return await hass.config_entries.flow.async_configure(
        result["flow_id"], fields or {}
    )


class TestUploadStep:
    """The flow asks for a profile before it asks anything else."""

    async def test_an_uploaded_profile_lands_in_the_directory(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """The whole point: a file on their computer becomes one of ours."""
        file_id = await browser.upload("acme.yaml", _body())

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == STEP_PROFILE_VENDOR
        path = Path(hass.config.path(CUSTOM_PROFILE_DIR, "acme.yaml"))
        assert await hass.async_add_executor_job(path.read_text) == _body()

    async def test_the_device_can_be_added_straight_after(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Importing is a step on the way in, not an errand of its own."""
        file_id = await browser.upload("acme.yaml", _body())
        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        # The imported file is a product to pick right away, under its vendor.
        assert "acme.yaml" in result["description_placeholders"]["result"]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {FIELD_VENDOR: "acme"}
        )
        assert result["step_id"] == STEP_PROFILE_SELECT

        with patch(
            "custom_components.esp_weaver.flows.local.async_discover_devices",
            new_callable=AsyncMock,
            return_value=[],
        ) as discover:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {FIELD_PROFILE: "local:acme.yaml"}
            )

        # Nothing on the network to find, which is as far as this needs to go:
        # the point is that picking the product went looking for the device.
        discover.assert_awaited_once()
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == ABORT_NO_DEVICES

    async def test_a_name_the_browser_made_up_is_accepted(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Downloading a profile twice is how a name like that comes about.

        Which makes it the likeliest one of all to arrive here.
        """
        file_id = await browser.upload("acme (1).yaml", _body())

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        path = Path(hass.config.path(CUSTOM_PROFILE_DIR, "acme (1).yaml"))
        assert await hass.async_add_executor_job(path.read_text) == _body()

    async def test_a_name_that_is_not_english_is_accepted(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Nobody naming a profile for their own device owes us English."""
        file_id = await browser.upload("灯泡配置.yaml", _body())

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        path = Path(hass.config.path(CUSTOM_PROFILE_DIR, "灯泡配置.yaml"))
        assert await hass.async_add_executor_job(path.read_text) == _body()

    async def test_the_next_step_says_what_was_imported(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """The step it lands on is the only screen left to report it on."""
        file_id = await browser.upload("my_valves.yml", _body())

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert "my_valves.yml" in result["description_placeholders"]["result"]

    async def test_skipping_it_says_nothing(self, hass: HomeAssistant) -> None:
        """A confirmation of an import that did not happen is noise."""
        result = await _import(hass, {})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        assert result["description_placeholders"]["result"] == ""

    async def test_the_uploaded_profile_is_the_one_that_loads(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Landing in the directory is only useful if the loader reads it."""
        file_id = await browser.upload("acme.yaml", _body())
        await _import(hass, {FIELD_PROFILE_FILE: file_id})

        products, _ = await hass.async_add_executor_job(
            scan_local, Path(hass.config.path(CUSTOM_PROFILE_DIR)), "local"
        )

        assert [p.id for p in products] == ["acme/valve"]

    async def test_the_upload_is_cleaned_up(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """The temporary copy has no reason to outlive the import."""
        file_id = await browser.upload("acme.yaml", _body())
        assert browser.directory(file_id).is_dir()

        await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert not browser.directory(file_id).exists()

    @pytest.mark.usefixtures("repository_source_offered")
    async def test_an_upload_and_a_repository_ref_together(
        self, hass: HomeAssistant, browser: Browser, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Both fields take effect: the file is saved and the ref followed."""
        serve_ref(aioclient_mock, product_doc())
        file_id = await browser.upload("acme.yaml", _body())

        result = await _import(
            hass,
            {FIELD_PROFILE_FILE: file_id, FIELD_REPOSITORY_SOURCE: "123"},
        )

        assert result["step_id"] == STEP_PROFILE_VENDOR
        assert Path(hass.config.path(CUSTOM_PROFILE_DIR, "acme.yaml")).is_file()
        assert (await async_get_mirror(hass)).source == "#123"

    @pytest.mark.usefixtures("repository_source_offered")
    async def test_a_source_already_followed_is_not_read_again(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Typing the same pull request twice costs one read, not two."""
        serve_ref(aioclient_mock, product_doc())
        await _import(hass, {FIELD_REPOSITORY_SOURCE: "123"})
        reads = aioclient_mock.call_count

        with patch(
            "custom_components.esp_weaver.flows.flow.async_refresh_catalog",
            new_callable=AsyncMock,
        ) as refresh:
            result = await _import(hass, {FIELD_REPOSITORY_SOURCE: "#123"})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        refresh.assert_not_awaited()
        assert (await async_get_mirror(hass)).source == "#123"
        # The vendor step reads the copy; the user step asked for nothing.
        assert aioclient_mock.call_count >= reads

    async def test_going_back_and_on_without_a_file_reports_no_import(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """The confirmation belongs to the submission that imported, not the flow."""
        file_id = await browser.upload("acme.yaml", _body())
        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})
        assert "acme.yaml" in result["description_placeholders"]["result"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {FIELD_ACTION: NAV_ACTION_BACK}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

        assert result["step_id"] == STEP_PROFILE_VENDOR
        placeholders = result["description_placeholders"]
        assert placeholders is not None
        assert placeholders["result"] == ""


class TestUploadRefusals:
    """A rejected upload leaves the directory as it was, and says why."""

    @staticmethod
    def _files(hass: HomeAssistant) -> list[str]:
        """Return what is in the vendor directory."""
        directory = Path(hass.config.path(CUSTOM_PROFILE_DIR))
        return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []

    async def test_a_profile_that_fails_validation_is_refused(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """A profile that landed anyway would raise a repair on every load."""
        file_id = await browser.upload("acme.yaml", "devices: 3\n")

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "upload_invalid"}
        assert self._files(hass) == []

    async def test_the_reason_is_shown_on_the_form(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Every way to fail here is one they can fix without starting over."""
        file_id = await browser.upload("acme.yaml", "devices: 3\n")

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        placeholders = result["description_placeholders"]
        assert placeholders["filename"] == "acme.yaml"
        assert placeholders["detail"]

    async def test_the_reason_is_left_for_the_frontend_to_translate(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Only the filename and the parser's words are placeholders."""
        hass.config.language = "zh-Hans"
        file_id = await browser.upload("acme.yaml", "devices: 3\n")

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": "upload_invalid"}
        assert set(result["description_placeholders"]) == {"filename", "detail"}

    async def test_something_that_is_not_yaml_is_refused(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Picking the wrong file is the likeliest mistake of all."""
        file_id = await browser.upload("acme.yaml", "{{{ not yaml\n")

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": "upload_invalid"}
        assert self._files(hass) == []

    async def test_a_file_that_is_not_a_yaml_name_is_refused(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """The name becomes a path in the configuration directory."""
        file_id = await browser.upload("notes.txt", _body())

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": "import_bad_filename"}
        assert result["description_placeholders"]["filename"] == "notes.txt"
        assert self._files(hass) == []

    async def test_something_far_too_large_is_refused(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """File upload takes a hundred megabytes and accept is only a hint."""
        oversized = "# padding\n" * (MAX_PROFILE_SIZE // 10 + 1)
        file_id = await browser.upload("acme.yaml", oversized)

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": "upload_too_large"}
        assert result["description_placeholders"]["limit"] == str(
            MAX_PROFILE_SIZE // 1024
        )
        assert self._files(hass) == []

    async def test_a_file_that_is_not_text_is_refused(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Nothing stops the file picker being pointed at an image."""
        file_id = await browser.upload("acme.yaml", b"\x89PNG\r\n\x1a\n\xff\xfe")

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": "upload_not_text"}
        assert self._files(hass) == []

    async def test_an_expired_upload_asks_for_the_file_again(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Restarting drops the temporary file, and a form can outlive one.

        Nothing was uploaded since, so the id the form still holds is one
        file_upload has never heard of - which is what a restart leaves behind.
        """
        result = await _import(hass, {FIELD_PROFILE_FILE: ulid_hex()})

        assert result["errors"] == {"base": "upload_expired"}

    async def test_an_upload_that_cannot_be_read_is_reported(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """file_upload still lists it, but the file itself is gone.

        Temporary directories get swept - by the system, by a cleaner - and a
        form left open long enough is how the sweep gets between the upload
        and the import. The reason is shown; nothing is written.
        """
        file_id = await browser.upload("acme.yaml", _body())
        (browser.directory(file_id) / "acme.yaml").unlink()

        result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": "upload_unreadable"}
        assert "acme.yaml" in result["description_placeholders"]["error"]
        assert self._files(hass) == []

    async def test_an_unexpected_failure_falls_back_to_the_generic_line(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """A bare translation key on the form would say nothing to anyone."""
        file_id = await browser.upload("acme.yaml", _body())

        with patch(
            "custom_components.esp_weaver.flows.flow.async_save_profile",
            side_effect=HomeAssistantError("the disk caught fire"),
        ):
            result = await _import(hass, {FIELD_PROFILE_FILE: file_id})

        assert result["errors"] == {"base": ERROR_PROFILE_IMPORT_FAILED}
        assert result["description_placeholders"]["error"] == "the disk caught fire"

    async def test_an_existing_profile_is_kept(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Overwriting silently would lose a mapping that worked."""
        first = await browser.upload("acme.yaml", _body())
        await _import(hass, {FIELD_PROFILE_FILE: first})
        second = await browser.upload(
            "acme.yaml", "version: 1\nvendor: acme\nproduct: thing\nmodel: T1\n"
        )

        result = await _import(hass, {FIELD_PROFILE_FILE: second})

        assert result["errors"] == {"base": "import_exists"}
        path = Path(hass.config.path(CUSTOM_PROFILE_DIR, "acme.yaml"))
        assert await hass.async_add_executor_job(path.read_text) == _body()

    async def test_overwrite_replaces_it(
        self, hass: HomeAssistant, browser: Browser
    ) -> None:
        """Correcting a profile in place is the reason the option exists."""
        corrected = yaml.safe_dump(
            {**PROFILE, "devices": [{**PROFILE["devices"][0], "type": "esp.device.x"}]}
        )
        first = await browser.upload("acme.yaml", _body())
        await _import(hass, {FIELD_PROFILE_FILE: first})
        second = await browser.upload("acme.yaml", corrected)

        result = await _import(
            hass, {FIELD_PROFILE_FILE: second, FIELD_OVERWRITE: True}
        )

        assert result["step_id"] == STEP_PROFILE_VENDOR
        path = Path(hass.config.path(CUSTOM_PROFILE_DIR, "acme.yaml"))
        assert await hass.async_add_executor_job(path.read_text) == corrected


class TestRefusalWording:
    """What the user is told has to match how the file got here."""

    @staticmethod
    def _errors(catalogue: str) -> dict[str, str]:
        """Return the config flow errors from one translation file."""
        root = Path(__file__).parents[2] / "custom_components" / DOMAIN
        text = (root / catalogue).read_text(encoding="utf-8")
        return json.loads(text)["config"]["error"]

    @pytest.mark.parametrize(
        "catalogue",
        ["strings.json", "translations/en.json", "translations/zh-Hans.json"],
    )
    def test_every_refusal_has_something_to_say(self, catalogue: str) -> None:
        """The form shows the key itself when the catalogue has no sentence."""
        errors = self._errors(catalogue)

        assert set(errors) >= PROFILE_IMPORT_ERRORS
        assert ERROR_PROFILE_IMPORT_FAILED in errors

    async def test_an_upload_is_not_called_a_download(
        self, hass: HomeAssistant
    ) -> None:
        """Both paths share the check, so only the key keeps them apart."""
        with pytest.raises(ServiceValidationError, match="upload_invalid"):
            await async_save_profile(hass, "acme.yaml", "devices: 3\n")

    async def test_a_download_is_still_called_a_download(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """The pasted-link path keeps the wording it always had."""
        aioclient_mock.get(URL, text="devices: 3\n")

        with pytest.raises(ServiceValidationError, match="import_invalid"):
            await async_import_profile(hass, URL)
