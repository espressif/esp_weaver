# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The shipped Espressif profiles are the repository's, byte for byte."""

from pathlib import Path
import subprocess
import sys

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_shipped_profiles.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the tool as CI does."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _pair(tmp_path: Path, repository: dict[str, str], shipped: dict[str, str]):
    """Lay out a repository checkout and a shipped directory; return the arguments naming them."""
    for directory, files in (
        (Path("repository") / "vendors" / "espressif", repository),
        (Path("shipped"), shipped),
    ):
        (tmp_path / directory).mkdir(parents=True)
        for name, text in files.items():
            (tmp_path / directory / name).write_text(text, encoding="utf-8")
    return (
        "--repository",
        str(tmp_path / "repository"),
        "--shipped",
        str(tmp_path / "shipped"),
    )


class TestTheCheckedInCopies:
    """What the repository holds today."""

    def test_the_shipped_copy_matches_the_repository(self) -> None:
        """The shipped copy is edited in the repository; a checkout at hand is compared."""
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
    """Every way the two directories can disagree is reported."""

    def test_identical_directories_pass(self, tmp_path: Path) -> None:
        """Nothing to say when nothing differs."""
        result = _run(*_pair(tmp_path, {"a.yaml": "x\n"}, {"a.yaml": "x\n"}))

        assert result.returncode == 0

    def test_every_kind_of_difference_is_named(self, tmp_path: Path) -> None:
        """Changed, missing and extra files are each one line; exit is non-zero."""
        result = _run(
            *_pair(
                tmp_path,
                {"a.yaml": "x\n", "b.yaml": "same\n", "new.yaml": "n\n"},
                {"a.yaml": "y\n", "b.yaml": "same\n", "old.yaml": "o\n"},
            )
        )

        assert result.returncode == 1
        assert "a.yaml: differs" in result.stdout
        assert "new.yaml: in the repository only" in result.stdout
        assert "old.yaml: shipped only" in result.stdout
        assert "b.yaml" not in result.stdout
        assert "--sync" in result.stdout

    def test_a_reformatted_file_differs(self, tmp_path: Path) -> None:
        """The hash is over bytes, so whitespace is a difference too."""
        result = _run(*_pair(tmp_path, {"a.yaml": "k: v\n"}, {"a.yaml": "k:  v\n"}))

        assert result.returncode == 1

    def test_only_profile_files_count(self, tmp_path: Path) -> None:
        """A README beside the profiles is not part of what is shipped."""
        result = _run(
            *_pair(tmp_path, {"a.yaml": "x\n", "README.md": "r\n"}, {"a.yaml": "x\n"})
        )

        assert result.returncode == 0


class TestTheSync:
    """``--sync`` makes the shipped copy an exact copy."""

    def test_sync_copies_replaces_and_removes(self, tmp_path: Path) -> None:
        """Afterwards the check passes and says so."""
        args = _pair(
            tmp_path,
            {"a.yaml": "x\n", "new.yaml": "n\n"},
            {"a.yaml": "y\n", "old.yaml": "o\n"},
        )

        result = _run(*args, "--sync")

        assert result.returncode == 0
        shipped = tmp_path / "shipped"
        assert (shipped / "a.yaml").read_text() == "x\n"
        assert (shipped / "new.yaml").read_text() == "n\n"
        assert not (shipped / "old.yaml").exists()
        assert _run(*args).returncode == 0
