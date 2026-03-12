# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""Security1 implementation for protocomm (X25519 + AES-CTR).

APIs for interpreting and creating protobuf packets for
protocomm endpoint with security type protocomm_security1.
"""

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .. import protocol as proto
from ..utils import long_to_bytes, str_to_bytes
from .security import Security

__all__ = ["Security1"]


def xor_bytes(bytes_a: bytes, bytes_b: bytes) -> bytes:
    """XOR two byte sequences of equal length."""
    return b"".join(long_to_bytes(bytes_a[i] ^ bytes_b[i]) for i in range(len(bytes_b)))


class SecurityState:
    """Enum for state of protocomm_security1 FSM."""

    REQUEST1 = 0
    RESPONSE1_REQUEST2 = 1
    RESPONSE2 = 2
    FINISHED = 3


class Security1(Security):
    """Security1 implementation using X25519 key exchange and AES-CTR encryption.

    This security scheme provides encrypted communication using:
    - X25519 for key exchange
    - Optional proof of possession (PoP) for additional security
    - AES-256-CTR for data encryption
    """

    def __init__(self, pop: str = "", verbose: bool = False):
        """Initialize Security1.

        Args:
            pop: Proof of possession string.
            verbose: Enable verbose logging.
        """
        # Initialize state of the security1 FSM
        self.session_state = SecurityState.REQUEST1
        self.pop = str_to_bytes(pop)
        self.verbose = verbose
        # Initialize keys and ciphers (set during handshake)
        self.client_private_key = None
        self.client_public_key = None
        self.device_public_key = None
        self.cipher_encrypt = None
        self.cipher_decrypt = None
        Security.__init__(self, self.security1_session)

    def security1_session(self, response_data):
        """Handle security1 session FSM.

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

    def __generate_key(self):
        """Generate private and public key pair for client."""
        self.client_private_key = X25519PrivateKey.generate()
        self.client_public_key = self.client_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )

    def _print_verbose(self, data: str) -> None:
        """Print verbose debug output if enabled.

        Args:
            data: Debug message to print.
        """
        # Verbose output disabled for Home Assistant integration
        # In production, this would log to a debug logger
        _ = data  # Acknowledge the parameter

    def setup0_request(self):
        """Form SessionCmd0 request packet using client public key."""
        setup_req = proto.session_pb2.SessionData()
        setup_req.sec_ver = proto.session_pb2.SecScheme1
        self.__generate_key()
        setup_req.sec1.sc0.client_pubkey = self.client_public_key
        self._print_verbose(f"Client Public Key:\t0x{self.client_public_key.hex()}")
        return setup_req.SerializeToString().decode("latin-1")

    def setup0_response(self, response_data):
        """Interpret SessionResp0 response packet and derive shared key.

        Args:
            response_data: Response data from device.

        Raises:
            RuntimeError: If security scheme doesn't match.
        """
        setup_resp = proto.session_pb2.SessionData()
        setup_resp.ParseFromString(str_to_bytes(response_data))
        self._print_verbose("Security version:\t" + str(setup_resp.sec_ver))
        if setup_resp.sec_ver != proto.session_pb2.SecScheme1:
            raise RuntimeError("Incorrect security scheme")

        self.device_public_key = setup_resp.sec1.sr0.device_pubkey
        # Device random is the initialization vector
        device_random = setup_resp.sec1.sr0.device_random
        self._print_verbose(f"Device Public Key:\t0x{self.device_public_key.hex()}")
        self._print_verbose(f"Device Random:\t0x{device_random.hex()}")

        # Calculate Curve25519 shared key
        shared_key = self.client_private_key.exchange(
            X25519PublicKey.from_public_bytes(self.device_public_key)
        )
        self._print_verbose(f"Shared Key:\t0x{shared_key.hex()}")

        # If PoP is provided, XOR SHA256 of PoP with the previously
        # calculated Shared Key to form the actual Shared Key
        if len(self.pop) > 0:
            # Calculate SHA256 of PoP
            h = hashes.Hash(hashes.SHA256(), backend=default_backend())
            h.update(self.pop)
            digest = h.finalize()
            # XOR with and update Shared Key
            shared_key = xor_bytes(shared_key, digest)
            self._print_verbose(
                f"Updated Shared Key (XORed with PoP):\t0x{shared_key.hex()}"
            )
        # Initialize separate encryption and decryption engines
        cipher_encrypt = Cipher(
            algorithms.AES(shared_key),
            modes.CTR(device_random),
            backend=default_backend(),
        )
        cipher_decrypt = Cipher(
            algorithms.AES(shared_key),
            modes.CTR(device_random),
            backend=default_backend(),
        )
        self.cipher_encrypt = cipher_encrypt.encryptor()
        self.cipher_decrypt = cipher_decrypt.decryptor()

    def setup1_request(self):
        """Form SessionCmd1 request packet using encrypted device public key."""
        setup_req = proto.session_pb2.SessionData()
        setup_req.sec_ver = proto.session_pb2.SecScheme1
        setup_req.sec1.msg = proto.sec1_pb2.Session_Command1
        # Encrypt device public key and attach to the request packet
        client_verify = self.cipher_encrypt.update(self.device_public_key)
        self._print_verbose(f"Client Proof:\t0x{client_verify.hex()}")
        setup_req.sec1.sc1.client_verify_data = client_verify
        return setup_req.SerializeToString().decode("latin-1")

    def setup1_response(self, response_data):
        """Interpret SessionResp1 response packet and verify device.

        Args:
            response_data: Response data from device.

        Raises:
            RuntimeError: If device verification fails or protocol unsupported.
        """
        setup_resp = proto.session_pb2.SessionData()
        setup_resp.ParseFromString(str_to_bytes(response_data))
        # Ensure security scheme matches
        if setup_resp.sec_ver == proto.session_pb2.SecScheme1:
            # Read encrypted device verify string
            device_verify = setup_resp.sec1.sr1.device_verify_data
            self._print_verbose(f"Device Proof:\t0x{device_verify.hex()}")
            # Synchronize decrypt offset to match where device encrypt started
            self.cipher_decrypt.update(bytes(len(self.device_public_key)))
            enc_client_pubkey = self.cipher_decrypt.update(
                setup_resp.sec1.sr1.device_verify_data
            )

            # Match decrypted string with client public key
            if enc_client_pubkey != self.client_public_key:
                raise RuntimeError("Failed to verify device!")
        else:
            raise RuntimeError("Unsupported security protocol")

    def encrypt_data(self, data):
        """Encrypt data using AES-CTR.

        Args:
            data: Data to encrypt.

        Returns:
            Encrypted data.
        """
        return self.cipher_encrypt.update(data)

    def decrypt_data(self, data):
        """Decrypt data using AES-CTR.

        Args:
            data: Data to decrypt.

        Returns:
            Decrypted data.
        """
        return self.cipher_decrypt.update(data)
