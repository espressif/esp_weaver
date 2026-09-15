# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The tool CI runs over a repository, in the modes the workflows use."""

import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from custom_components.esp_weaver.profiles.index import GITHUB_RAW, parse_index
from tests.github_fake import COMMIT, FORK, product_doc

TOOL = Path(__file__).resolve().parents[2] / "tools" / "build_profile_index.py"


def _repository(root: Path, *docs: dict) -> Path:
    """Lay the documents out as a repository under ``root``."""
    for doc in docs:
        path = root / "vendors" / doc["vendor"] / f"{doc['product']}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    (root / "vendors").mkdir(parents=True, exist_ok=True)
    return root


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the tool as CI does."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


class TestThePullRequestMode:
    """What profile_index.yml publishes for the integration to read."""

    def test_the_index_names_the_pull_request_and_reads_back(
        self, tmp_path: Path
    ) -> None:
        """The source is what lets the integration locate files by commit."""
        root = _repository(tmp_path / "repo", product_doc())
        out = tmp_path / "out" / "index.json"

        result = _run(
            str(root),
            "--pull",
            "123",
            "--repo",
            FORK,
            "--commit",
            COMMIT,
            "--output",
            str(out),
        )

        assert result.returncode == 0, result.stderr
        parsed = parse_index(out.read_text(encoding="utf-8"), None)
        assert parsed.error is None
        assert parsed.source is not None
        assert (parsed.source.pull, parsed.source.repo, parsed.source.commit) == (
            123,
            FORK,
            COMMIT,
        )
        [plug] = parsed.products
        assert plug.url == (f"{GITHUB_RAW}/{FORK}/{COMMIT}/vendors/acme/plug.yaml")

    def test_a_refusal_is_written_into_the_index_and_fails_the_run(
        self, tmp_path: Path
    ) -> None:
        """The pull request shows red, and the form shows the same words."""
        root = _repository(tmp_path / "repo", product_doc(label="Padded "))
        out = tmp_path / "out" / "index.json"

        result = _run(
            str(root),
            "--pull",
            "123",
            "--repo",
            FORK,
            "--commit",
            COMMIT,
            "--output",
            str(out),
        )

        assert result.returncode == 1
        assert "starts or ends with whitespace" in result.stderr
        document = json.loads(out.read_text(encoding="utf-8"))
        assert document["products"] == []
        assert "starts or ends with whitespace" in document["error"]
        assert document["source"]["pull"] == 123

    @pytest.mark.parametrize(
        "args",
        [
            ("--pull", "123"),
            ("--repo", FORK),
            ("--pull", "123", "--repo", FORK, "--commit", COMMIT),
        ],
    )
    def test_the_pull_request_options_go_together(
        self, tmp_path: Path, args: tuple[str, ...]
    ) -> None:
        """Half a source would publish an index nothing can read."""
        root = _repository(tmp_path / "repo")

        result = _run(str(root), *args)

        assert result.returncode == 2
        assert "--pull" in result.stderr


class TestTheToolNeedsNoHomeAssistant:
    """The profile repository's CI runs the tool with PyYAML and voluptuous only."""

    def test_the_tool_runs_where_home_assistant_cannot_be_imported(
        self, tmp_path: Path
    ) -> None:
        """Importing ``homeassistant`` is made to fail; the tool still judges."""
        root = _repository(tmp_path / "repo", product_doc())
        script = (
            "import runpy, sys\n"
            "sys.modules['homeassistant'] = None\n"
            f"sys.argv = ['tool', {str(root)!r}, '--validate']\n"
            f"runpy.run_path({str(TOOL)!r}, run_name='__main__')\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        assert "every profile validates" in result.stdout
