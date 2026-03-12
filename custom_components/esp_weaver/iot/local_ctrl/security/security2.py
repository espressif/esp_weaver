# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Security2 implementation for protocomm (SRP6a + AES-GCM).

APIs for interpreting and creating protobuf packets for
protocomm endpoint with security type protocomm_security2.

This module implements the security2 protocol using SRP6a and AES-GCM.
"""
import logging
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import protocol as proto
from ..utils import long_to_bytes, str_to_bytes
from .security import Security
from .srp6a import Srp6a, generate_salt_and_verifier

__all__ = ["Security2", "sec2_gen_salt_verifier"]

AES_KEY_LEN = 256 // 8

_LOGGER = logging.getLogger(__name__)


class SecurityState:
    """Enum for state of protocomm_security2 FSM."""

    REQUEST1 = 0
    RESPONSE1_REQUEST2 = 1
    RESPONSE2 = 2
    FINISHED = 3


def sec2_gen_salt_verifier(username: str, password: str, salt_len: int) -> None:
    """Generate salt and verifier for security2 authentication.

    Args:
        username: Username for authentication.
        password: Password for authentication.
        salt_len: Length of salt in bytes.
    """
    salt, verifier = generate_salt_and_verifier(username, password, len_s=salt_len)

    salt_str = ", ".join([format(b, "#04x") for b in salt])
    salt_c_arr = "\n    ".join(
        salt_str[i : i + 96] for i in range(0, len(salt_str), 96)
    )
    _LOGGER.info("static const char sec2_salt[] = {\n    %s\n};\n", salt_c_arr)

    verifier_str = ", ".join([format(b, "#04x") for b in verifier])
    verifier_c_arr = "\n    ".join(
        verifier_str[i : i + 96] for i in range(0, len(verifier_str), 96)
    )
    _LOGGER.info(
        "static const char sec2_verifier[] = {\n    %s\n};\n", verifier_c_arr
    )


class Security2(Security):
    """Security2 implementation using SRP6a and AES-GCM encryption.

    This security scheme provides encrypted communication using:
    - SRP6a for secure password authentication
    - AES-256-GCM for authenticated encryption
    """

    def __init__(
        self,
        sec_patch_ver: int,
        username: str,
        password: str,
        verbose: bool = False,
    ) -> None:
        """Initialize Security2.

        Args:
            sec_patch_ver: Security patch version.
            username: Username for SRP6a authentication.
            password: Password for SRP6a authentication.
            verbose: Enable verbose logging.
        """
        # Initialize state of the security2 FSM
        self.session_state = SecurityState.REQUEST1
        self.sec_patch_ver = sec_patch_ver
        self.username = username
        self.password = password
        self.verbose = verbose

        self.srp6a_ctx: Srp6a | None = None
        self.cipher: AESGCM | None = None

        self.client_pop_key = None
        self.nonce = bytearray()

        super().__init__(self.security2_session)

    def security2_session(self, response_data: bytes) -> Any:
        """Handle security2 session FSM.

        Interprets/forms protobuf packets according to present state of session.

        Args:
            response_data: Response data from device.

        Returns:
            Request data or None if session complete.
        """
        if self.session_state == SecurityState.REQUEST1:
            self.session_state = SecurityState.RESPONSE1_REQUEST2
            return self.setup0_request()

        if self.session_state == SecurityState.RESPONSE1_REQUEST2:
            self.session_state = SecurityState.RESPONSE2
            self.setup0_response(response_data)
            return self.setup1_request()

        if self.session_state == SecurityState.RESPONSE2:
            self.session_state = SecurityState.FINISHED
            self.setup1_response(response_data)
            return None

        return None

    def _print_verbose(self, data: str) -> None:
        """Print verbose debug output if enabled.

        Args:
            data: Debug message to print.
        """
        # Verbose output disabled for Home Assistant integration
        # In production, this would log to a debug logger
        _ = data  # Acknowledge the parameter

    def setup0_request(self) -> Any:
        """Form SessionCmd0 request packet using client public key."""
        setup_req = proto.session_pb2.SessionData()
        setup_req.sec_ver = proto.session_pb2.SecScheme2
        setup_req.sec2.msg = proto.sec2_pb2.S2Session_Command0

        setup_req.sec2.sc0.client_username = str_to_bytes(self.username)
        self.srp6a_ctx = Srp6a(self.username, self.password)
        if self.srp6a_ctx is None:
            raise RuntimeError("Failed to initialize SRP6a instance!")

        client_pubkey = long_to_bytes(self.srp6a_ctx.public_ephemeral)
        setup_req.sec2.sc0.client_pubkey = client_pubkey

        self._print_verbose(f"Client Public Key:\t0x{client_pubkey.hex()}")
        return setup_req.SerializeToString().decode("latin-1")

    def setup0_response(self, response_data: bytes) -> None:
        """Interpret SessionResp0 response packet.

        Args:
            response_data: Response data from device.

        Raises:
            RuntimeError: If security scheme doesn't match.
        """
        setup_resp = proto.session_pb2.SessionData()
        setup_resp.ParseFromString(str_to_bytes(response_data))
        self._print_verbose(f"Security version:\t{setup_resp.sec_ver!s}")
        if setup_resp.sec_ver != proto.session_pb2.SecScheme2:
            raise RuntimeError("Incorrect security scheme")

        # Device public key, random salt and password verifier
        device_pubkey = setup_resp.sec2.sr0.device_pubkey
        device_salt = setup_resp.sec2.sr0.device_salt

        self._print_verbose(f"Device Public Key:\t0x{device_pubkey.hex()}")
        self.client_pop_key = self.srp6a_ctx.process_challenge(
            device_salt, device_pubkey
        )

    def setup1_request(self) -> Any:
        """Form SessionCmd1 request packet using encrypted device public key."""
        setup_req = proto.session_pb2.SessionData()
        setup_req.sec_ver = proto.session_pb2.SecScheme2
        setup_req.sec2.msg = proto.sec2_pb2.S2Session_Command1

        # Encrypt device public key and attach to the request packet
        if self.client_pop_key is None:
            raise RuntimeError("Failed to generate client proof!")

        self._print_verbose(f"Client Proof:\t0x{self.client_pop_key.hex()}")
        setup_req.sec2.sc1.client_proof = self.client_pop_key

        return setup_req.SerializeToString().decode("latin-1")

    def setup1_response(self, response_data: bytes) -> Any:
        """Interpret SessionResp1 response packet and initialize cipher.

        Args:
            response_data: Response data from device.

        Raises:
            RuntimeError: If device verification fails or protocol unsupported.
        """
        setup_resp = proto.session_pb2.SessionData()
        setup_resp.ParseFromString(str_to_bytes(response_data))
        # Ensure security scheme matches
        if setup_resp.sec_ver == proto.session_pb2.SecScheme2:
            # Read encrypted device proof string
            device_proof = setup_resp.sec2.sr1.device_proof
            self._print_verbose(f"Device Proof:\t0x{device_proof.hex()}")
            self.srp6a_ctx.verify_session(device_proof)
            if not self.srp6a_ctx.authenticated():
                raise RuntimeError("Failed to verify device proof")
        else:
            raise RuntimeError("Unsupported security protocol")

        # Getting the shared secret
        shared_secret = self.srp6a_ctx.get_session_key()
        self._print_verbose(f"Shared Secret:\t0x{shared_secret.hex()}")

        # Using the first 256 bits of a 512 bit key
        session_key = shared_secret[:AES_KEY_LEN]
        self._print_verbose(f"Session Key:\t0x{session_key.hex()}")

        # 96-bit nonce
        self.nonce = bytearray(setup_resp.sec2.sr1.device_nonce)
        if self.nonce is None:
            raise RuntimeError("Received invalid nonce from device!")
        self._print_verbose(f"Nonce:\t0x{self.nonce.hex()}")

        # Initialize the encryption engine with Shared Key and initialization vector
        self.cipher = AESGCM(session_key)
        if self.cipher is None:
            raise RuntimeError("Failed to initialize AES-GCM cryptographic engine!")

    def _increment_nonce(self) -> None:
        """Increment the last 4 bytes of nonce (big-endian counter)."""
        if self.sec_patch_ver == 1:
            # Read last 4 bytes as big-endian integer
            counter = struct.unpack(">I", bytes(self.nonce[8:]))[0]
            counter += 1  # Increment counter
            if counter > 0xFFFFFFFF:  # Check for overflow
                raise RuntimeError("Nonce counter overflow")
            self.nonce[8:] = struct.pack(">I", counter)  # Store back as big-endian

    def encrypt_data(self, data: bytes) -> Any:
        """Encrypt data using AES-GCM.

        Args:
            data: Data to encrypt.

        Returns:
            Encrypted data with authentication tag.
        """
        self._print_verbose(f"Nonce:\t0x{self.nonce.hex()}")
        ciphertext = self.cipher.encrypt(self.nonce, data, None)
        self._increment_nonce()
        return ciphertext

    def decrypt_data(self, data: bytes) -> Any:
        """Decrypt data using AES-GCM.

        Args:
            data: Data to decrypt (with authentication tag).

        Returns:
            Decrypted data.
        """
        self._print_verbose(f"Nonce:\t0x{self.nonce.hex()}")
        plaintext = self.cipher.decrypt(self.nonce, data, None)
        self._increment_nonce()
        return plaintext
