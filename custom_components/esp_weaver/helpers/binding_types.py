# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Turns the strings a profile declares into Home Assistant enums."""

from enum import Enum
import logging

from ..profiles import EntityBinding

_LOGGER = logging.getLogger(__name__)


def enum_member[EnumT: Enum](
    binding: EntityBinding, declared: str | None, enum: type[EnumT]
) -> EnumT | None:
    """Look one declared string up in an enum; a bad value is logged, not raised."""
    if declared is None:
        return None
    try:
        return enum(declared)
    except ValueError:
        _LOGGER.warning(
            "Profile entity %r declares %r, which is not a valid %s",
            binding.name,
            declared,
            enum.__name__,
        )
        return None


def device_class_of[EnumT: Enum](
    binding: EntityBinding, enum: type[EnumT]
) -> EnumT | None:
    """Return the binding's device_class as a member of a platform's enum."""
    return enum_member(binding, binding.device_class, enum)


def state_class_of[EnumT: Enum](
    binding: EntityBinding, enum: type[EnumT]
) -> EnumT | None:
    """Return the binding's state_class as a member of a platform's enum."""
    return enum_member(binding, binding.state_class, enum)


__all__ = ["device_class_of", "enum_member", "state_class_of"]
