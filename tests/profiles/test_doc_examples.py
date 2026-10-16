# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Every complete profile example in the guides must pass validation.

The guides are what customers copy from, so a broken example (a missing
``model``, a placeholder ``entities``) is a broken first run, and it has
been caught by human review three times. A yaml block counts as a
profile example when it parses to a mapping that carries the ``version``
and ``vendor`` keys, or a ``devices`` / ``services`` block: the latter
shape is a profile fragment (for example only a ``devices:`` section),
and is validated as a whole file too, so a fragment that cannot stand
as a complete profile fails here and must be completed or marked as
code, not yaml. Other yaml blocks in the docs are unrelated (for
example Home Assistant automations) and are left alone.
"""

from pathlib import Path
import re

import pytest
import yaml

from custom_components.esp_weaver.profiles.schema import validate_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _profile_examples() -> list[tuple[str, int, dict]]:
    """Collect (document, block number, parsed yaml) for every example."""
    examples: list[tuple[str, int, dict]] = []
    for document in sorted((REPO_ROOT / "docs").glob("*.md")):
        text = document.read_text(encoding="utf-8")
        for number, block in enumerate(YAML_BLOCK.findall(text), start=1):
            parsed = yaml.safe_load(block)
            if isinstance(parsed, dict) and (
                {"version", "vendor"} <= parsed.keys()
                or {"devices", "services"} & parsed.keys()
            ):
                examples.append((document.name, number, parsed))
    return examples


EXAMPLES = _profile_examples()


@pytest.mark.parametrize(
    ("document", "number", "parsed"),
    EXAMPLES,
    ids=[f"{document}#{number}" for document, number, _ in EXAMPLES],
)
def test_doc_example_validates(document: str, number: int, parsed: dict) -> None:
    """A copied-and-pasted example must be accepted as it stands."""
    validate_profile(parsed, f"{document} yaml block {number}")


def test_examples_are_found() -> None:
    """The extraction itself is guarded: no examples found is a failure."""
    documents = {document for document, _, _ in EXAMPLES}
    assert {
        "developing_a_product_profile.md",
        "developing_a_product_profile_zh.md",
        "device_profiles_in_detail.md",
        "device_profiles_in_detail_zh.md",
    } <= documents
