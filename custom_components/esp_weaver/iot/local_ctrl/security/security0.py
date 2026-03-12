# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""Security0 implementation for protocomm (no encryption).

APIs for interpreting and creating protobuf packets for
protocomm endpoint with security type protocomm_security0.
"""

from .. import protocol as proto
from ..utils import str_to_bytes
from .security import Security

__all__ = ["Security0"]


class Security0(Security):
    """Security0 implementation with no encryption.

    This security scheme provides no actual encryption and is intended
    for development/testing purposes only.
    """

    def __init__(self, verbose: bool = False):
        """Initialize Security0.

        Args:
            verbose: Enable verbose logging (kept for API compatibility).
        """
        # Initialize state of the security0 FSM
        # Note: verbose parameter kept for API compatibility with other security classes
        self._verbose = verbose
        self.session_state = 0
        super().__init__(self.security0_session)

    def security0_session(self, response_data):
        """Handle security0 session FSM.

        Interprets/forms protobuf packets according to present state of session.

        Args:
            response_data: Response data from device.

        Returns:
            Request data or None if session complete.
        """
        if self.session_state == 0:
            self.session_state = 1
            return self.setup0_request()
        if self.session_state == 1:
            self.setup0_response(response_data)
            return None
        return None

    def setup0_request(self):
        """Form protocomm security0 request packet."""
        setup_req = proto.session_pb2.SessionData()
        setup_req.sec_ver = 0
        session_cmd = proto.sec0_pb2.S0SessionCmd()
        setup_req.sec0.sc.MergeFrom(session_cmd)
        return setup_req.SerializeToString().decode("latin-1")

    def setup0_response(self, response_data):
        """Interpret protocomm security0 response packet.

        Args:
            response_data: Response data from device.

        Raises:
            RuntimeError: If security scheme doesn't match.
        """
        setup_resp = proto.session_pb2.SessionData()
        setup_resp.ParseFromString(str_to_bytes(response_data))
        # Check if security scheme matches
        if setup_resp.sec_ver != proto.session_pb2.SecScheme0:
            raise RuntimeError("Incorrect security scheme")

    def encrypt_data(self, data):
        """Encrypt data (passthrough for security0).

        Args:
            data: Data to encrypt.

        Returns:
            Unmodified data.
        """
        return data

    def decrypt_data(self, data):
        """Decrypt data (passthrough for security0).

        Args:
            data: Data to decrypt.

        Returns:
            Unmodified data.
        """
        return data
