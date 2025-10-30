# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""Base security class for protocomm.

This module defines the abstract base class for security implementations.
"""


class Security:
    """Base class for protocomm security implementations.

    Subclasses (Security0, Security1, Security2) must implement:
    - encrypt_data(data: bytes) -> bytes
    - decrypt_data(data: bytes) -> bytes
    """

    def __init__(self, security_session):
        """Initialize security with session handler.

        Args:
            security_session: Callback function for security session handling.
        """
        self.security_session = security_session
