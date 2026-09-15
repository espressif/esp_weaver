# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Constants of the ESP local control protocol itself.

Owned by this package so that it depends on nothing above it: what is here
is fixed by the protocol and the firmware, not by the integration using it.
"""

from typing import Final

# Security schemes, as the firmware numbers them: 0 = none, 1 = PoP, 2 = SRP6a.
SECURITY_MODE_SRP6A: Final[int] = 2

# sec2 (SRP6a) authenticates against a fixed username with the PoP as the
# password. The runtime connection and the config flow's PoP test must present
# the same one, or a PoP that passes setup fails every time it is used after.
SRP6A_USERNAME: Final[str] = "wifiprov"

# Timeouts, in seconds
HTTP_CONNECTION_TIMEOUT: Final[float] = 60.0
QUERY_TIMEOUT: Final[float] = 10.0
LISTENER_RECV_TIMEOUT: Final[float] = 60.0
LISTENER_ERROR_SLEEP: Final[float] = 1.0

# Buffer and message sizes
SOCKET_RECV_BUFFER_SIZE: Final[int] = 4096
HTTP_HEADER_MIN_LENGTH: Final[int] = 20

# HTTP header and response fields
HEADER_CONTENT_LENGTH: Final[str] = "Content-Length"
FIELD_STATUS: Final[str] = "status"
FIELD_ERROR: Final[str] = "error"
