# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""Convenience functions for commonly used data type conversions.

This module provides helper functions for converting between
bytes, integers, and strings.
"""

__all__ = ["bytes_to_long", "hex_str_to_bytes", "long_to_bytes", "str_to_bytes"]


def bytes_to_long(s: bytes) -> int:
    """Convert bytes to integer (big-endian).

    Args:
        s: Bytes to convert.

    Returns:
        Integer value.
    """
    return int.from_bytes(s, "big")


def long_to_bytes(n: int) -> bytes:
    """Convert integer to bytes (big-endian).

    Args:
        n: Integer to convert.

    Returns:
        Bytes representation.
    """
    if n == 0:
        return b"\x00"
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def str_to_bytes(s: str) -> bytes:
    """Convert string to bytes using latin-1 encoding.

    Example: 'deadbeef' -> b'deadbeef'

    Args:
        s: String to convert.

    Returns:
        Bytes representation.
    """
    return bytes(s, encoding="latin-1")


def hex_str_to_bytes(s: str) -> bytes:
    """Convert hex string to bytes.

    Example: 'deadbeef' -> b'\\xde\\xad\\xbe\\xef'

    Args:
        s: Hex string to convert.

    Returns:
        Bytes representation.
    """
    return bytes.fromhex(s)
