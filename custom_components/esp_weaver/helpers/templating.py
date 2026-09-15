# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Filling placeholders in text that came from a profile or a translation.

Unlike ``str.format``, only a bare field name with an optional format spec is
accepted: ``{state.__class__}`` and ``{device[0]}`` are refused, since profile
and translation text is not ours.
"""

from string import Formatter
from typing import Any

_FORMATTER = Formatter()


def render_template(template: str, fields: dict[str, Any]) -> str:
    """Fill ``{name}`` and ``{name:spec}`` placeholders from ``fields``.

    Raises:
        KeyError: A placeholder names a field that was not given.
        ValueError: A placeholder is not a bare name, or the template is malformed.
    """
    parts: list[str] = []
    for literal, name, spec, conversion in _FORMATTER.parse(template):
        parts.append(literal)
        if name is None:
            continue
        if not name.isidentifier():
            raise ValueError(f"placeholder {{{name}}} is not a plain field name")
        if name not in fields:
            raise KeyError(name)
        value = fields[name]
        if conversion is not None:
            value = _FORMATTER.convert_field(value, conversion)
        parts.append(format(value, spec or ""))
    return "".join(parts)
