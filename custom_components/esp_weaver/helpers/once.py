# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Saying a thing in the log once, however many times it is true."""

import logging
from typing import Any, Final

# Distinct messages remembered before the memory is dropped; bounded so a
# message with a never-repeating value cannot grow the set without limit.
_REMEMBERED_LIMIT: Final = 2048
_said: set[tuple[Any, ...]] = set()


def log_once(logger: logging.Logger, level: int, msg: str, *args: Any) -> None:
    """Log ``msg % args`` at ``level`` unless exactly that has been logged before.

    Arguments are compared by ``repr``, so lists and dicts need no hash.
    """
    key = (logger.name, level, msg, *(repr(arg) for arg in args))
    if key in _said:
        return
    if len(_said) >= _REMEMBERED_LIMIT:
        _said.clear()
    _said.add(key)
    logger.log(level, msg, *args)


def forget_said() -> None:
    """Forget what has been said, so that it may be said again. For tests."""
    _said.clear()
