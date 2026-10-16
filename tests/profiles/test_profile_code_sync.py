# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The profile repository's profilelib is the integration's profiles, byte for byte."""

from pathlib import Path
import subprocess
import sys

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_profile_code_sync.py"
MODULES = ("schema.py", "product.py", "index.py")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the tool as CI does."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _pair(tmp_path: Path, integration: dict[str, str], repository: dict[str, str]):
    """Lay out an integration profiles directory and a repository checkout."""
    mine = tmp_path / "profiles"
    theirs = tmp_path / "repository" / "tools" / "profilelib"
    mine.mkdir(parents=True)
    theirs.mkdir(parents=True)
    for name, text in integration.items():
        (mine / name).write_text(text, encoding="utf-8")
    for name, text in repository.items():
        (theirs / name).write_text(text, encoding="utf-8")
    return (
        "--repository",
        str(tmp_path / "repository"),
        "--integration",
        str(mine),
    )


class TestTheCheckedInCopies:
    """What the two repositories hold today."""

    def test_the_copies_are_identical(self) -> None:
        """A checkout at hand is compared; without one there is nothing to compare."""
        result = _run()

        if result.returncode == 2:
            pytest.skip(
                "no checkout of the profile repository; set ESP_WEAVER_PROFILES"
            )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_directory_that_is_not_a_checkout_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A wrong path is told apart from a real difference."""
        result = _run("--repository", str(tmp_path))

        assert result.returncode == 2
        assert "not a checkout" in result.stderr


class TestTheCheck:
    """Every way the two copies can disagree is reported."""

    def test_identical_directories_pass(self, tmp_path: Path) -> None:
        """Nothing to say when nothing differs."""
        files = dict.fromkeys(MODULES, "x\n")
        result = _run(*_pair(tmp_path, files, files))

        assert result.returncode == 0

    def test_every_kind_of_difference_is_named(self, tmp_path: Path) -> None:
        """Changed and missing modules are each one line; exit is non-zero."""
        result = _run(
            *_pair(
                tmp_path,
                {"schema.py": "x\n", "product.py": "same\n", "index.py": "i\n"},
                {"schema.py": "y\n", "product.py": "same\n"},
            )
        )

        assert result.returncode == 1
        assert "schema.py: differs" in result.stdout
        assert "index.py: in the integration only" in result.stdout
        assert "product.py" not in result.stdout
        assert "--sync" in result.stdout

    def test_a_comment_only_change_differs(self, tmp_path: Path) -> None:
        """The copies are byte for byte, so even a comment is a difference."""
        files = dict.fromkeys(MODULES, "x\n")
        changed = {**files, "schema.py": "x\n# note\n"}
        result = _run(*_pair(tmp_path, files, changed))

        assert result.returncode == 1
        assert "schema.py: differs" in result.stdout


class TestTheSync:
    """``--sync`` makes the repository copy an exact copy of the integration's."""

    def test_sync_copies_and_adds(self, tmp_path: Path) -> None:
        """Afterwards the check passes and says so."""
        args = _pair(
            tmp_path,
            {"schema.py": "x\n", "product.py": "p\n", "index.py": "i\n"},
            {"schema.py": "y\n"},
        )

        result = _run(*args, "--sync")

        assert result.returncode == 0
        theirs = tmp_path / "repository" / "tools" / "profilelib"
        assert (theirs / "schema.py").read_text() == "x\n"
        assert (theirs / "product.py").read_text() == "p\n"
        assert (theirs / "index.py").read_text() == "i\n"
        assert _run(*args).returncode == 0
