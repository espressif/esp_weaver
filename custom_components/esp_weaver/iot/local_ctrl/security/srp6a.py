# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""SRP-6a implementation for secure password authentication.

This module implements the Secure Remote Password protocol version 6a,
which provides zero-knowledge password proof for authentication.

SRP-6a Parameters:
- n_prime: A large safe prime (N = 2q+1, where q is prime)
- g: A generator modulo N
- k: Multiplier parameter (k = H(N, g) in SRP-6a)
- s: User's salt
- username: Username identifier
- p: Cleartext Password
- H(): One-way hash function
- u: Random scrambling parameter
- a, b: Secret ephemeral values
- A, B: Public ephemeral values
- x: Private key (derived from p and s)
- v: Password verifier
"""

import hashlib
import hmac
import os
from collections.abc import Callable
from typing import Any

from ..utils import bytes_to_long, long_to_bytes

SHA1 = 0
SHA224 = 1
SHA256 = 2
SHA384 = 3
SHA512 = 4

_hash_map = {
    SHA1: hashlib.sha1,
    SHA224: hashlib.sha224,
    SHA256: hashlib.sha256,
    SHA384: hashlib.sha384,
    SHA512: hashlib.sha512,
}

# The 3072-bit group from RFC 5054, the one esp_local_ctrl's sec2 uses. SRP
# defines others, but a client that only ever talks to that firmware needs
# only this one.
_N_HEX = """\
FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"""
_G_HEX = "5"


def get_ng() -> tuple[int, int]:
    """Return the group's (N, g) as integers."""
    return int(_N_HEX, 16), int(_G_HEX, 16)


def get_random(nbytes: int) -> int:
    """Get random integer with specified number of bytes.

    Args:
        nbytes: Number of random bytes.

    Returns:
        Random integer.
    """
    return bytes_to_long(os.urandom(nbytes))


def get_random_of_length(nbytes: int) -> int:
    """Get random integer with exactly specified bit length.

    Args:
        nbytes: Number of bytes.

    Returns:
        Random integer with MSB set.
    """
    offset = (nbytes * 8) - 1
    return get_random(nbytes) | (1 << offset)


def hash_args(hash_class: Callable, *args: Any, **kwargs: Any) -> int:
    """Hash multiple arguments into a single integer.

    Args:
        hash_class: Hash function class.
        *args: Values to hash.
        **kwargs: Optional 'width' for zero-padding.

    Returns:
        Hash digest as integer.
    """
    width = kwargs.get("width")

    h = hash_class()

    for s in args:
        if s is not None:
            data = long_to_bytes(s) if isinstance(s, int) else s
            if width is not None:
                h.update(bytes(width - len(data)))
            h.update(data)

    return int(h.hexdigest(), 16)


def hash_n_xor_g(hash_class: Callable, n_prime: int, g: int) -> bytes:
    """Compute H(N) XOR H(g) for SRP-6a M calculation.

    Args:
        hash_class: Hash function class.
        n_prime: Large prime N.
        g: Generator.

    Returns:
        XOR of hash digests.
    """
    bin_n = long_to_bytes(n_prime)
    bin_g = long_to_bytes(g)

    padding = len(bin_n) - len(bin_g)

    hash_n = hash_class(bin_n).digest()
    hash_g = hash_class(b"".join([b"\0" * padding, bin_g])).digest()

    return b"".join(long_to_bytes(hash_n[i] ^ hash_g[i]) for i in range(len(hash_n)))


def calculate_x(hash_class: Callable, salt: int, username: str, password: str) -> int:
    """Calculate private key x from salt, username, and password.

    Args:
        hash_class: Hash function class.
        salt: User's salt.
        username: Username.
        password: Password.

    Returns:
        Private key x.
    """
    username_bytes = username.encode()
    password_bytes = password.encode()

    return hash_args(
        hash_class, salt, hash_args(hash_class, username_bytes + b":" + password_bytes)
    )


def calculate_m(
    hash_class: Callable,
    n_prime: int,
    g: int,
    username: str,
    salt: int,
    pub_a: int,
    pub_b: int,
    session_key: bytes,
) -> bytes:
    """Calculate M proof value for SRP-6a.

    Args:
        hash_class: Hash function class.
        n_prime: Large prime N.
        g: Generator.
        username: Username.
        salt: User's salt.
        pub_a: Client public key A.
        pub_b: Server public key B.
        session_key: Session key K.

    Returns:
        M proof value.
    """
    username_bytes = username.encode()
    h = hash_class()
    h.update(hash_n_xor_g(hash_class, n_prime, g))
    h.update(hash_class(username_bytes).digest())
    h.update(long_to_bytes(salt))
    h.update(long_to_bytes(pub_a))
    h.update(long_to_bytes(pub_b))
    h.update(session_key)
    return h.digest()


def calculate_h_amk(
    hash_class: Callable, pub_a: int, m_proof: bytes, session_key: bytes
) -> bytes:
    """Calculate H(A, M, K) for server verification.

    Args:
        hash_class: Hash function class.
        pub_a: Client public key A.
        m_proof: M proof value.
        session_key: Session key K.

    Returns:
        H(A, M, K) value.
    """
    h = hash_class()
    h.update(long_to_bytes(pub_a))
    h.update(m_proof)
    h.update(session_key)
    return h.digest()


class Srp6a:
    """SRP-6a client implementation.

    This class implements the client side of the SRP-6a protocol
    for zero-knowledge password authentication.
    """

    def __init__(
        self,
        username: str,
        password: str,
        hash_alg: int = SHA512,
    ):
        """Initialize SRP-6a client.

        Args:
            username: Username for authentication.
            password: Password for authentication.
            hash_alg: Hash algorithm constant.
        """
        hash_class = _hash_map[hash_alg]

        n_prime, g = get_ng()
        k = hash_args(hash_class, n_prime, g, width=len(long_to_bytes(n_prime)))

        self.username = username
        self.password = password

        self.private_key = get_random_of_length(32)
        self.public_key = pow(g, self.private_key, n_prime)

        self.verifier: int | None = None
        self.session_key: bytes | None = None
        self.h_amk: bytes | None = None
        self._authenticated = False

        self.hash_class = hash_class
        self.n_prime = n_prime
        self.g = g
        self.k = k

    @property
    def public_ephemeral(self) -> int:
        """Get public ephemeral value A (alias for public_key)."""
        return self.public_key

    def authenticated(self) -> bool:
        """Check if authentication was successful.

        Returns:
            True if authenticated, False otherwise.
        """
        return self._authenticated

    def get_session_key(self) -> bytes | None:
        """Get session key if authenticated.

        Returns:
            Session key or None if not authenticated.
        """
        return self.session_key if self._authenticated else None

    def process_challenge(self, bytes_s: bytes, bytes_b: bytes) -> bytes | None:
        """Process server challenge and compute M proof.

        Args:
            bytes_s: Salt from server.
            bytes_b: Server public key B.

        Returns:
            M proof or None if SRP-6a safety check fails.
        """
        salt = bytes_to_long(bytes_s)
        pub_b = bytes_to_long(bytes_b)

        n_prime = self.n_prime
        g = self.g
        k = self.k

        hash_class = self.hash_class

        # SRP-6a safety check
        if (pub_b % n_prime) == 0:
            return None

        u = hash_args(
            hash_class, self.public_key, pub_b, width=len(long_to_bytes(n_prime))
        )
        if u == 0:  # SRP-6a safety check
            return None

        x = calculate_x(hash_class, salt, self.username, self.password)

        v = pow(g, x, n_prime)

        s = pow((pub_b - k * v), (self.private_key + u * x), n_prime)

        self.session_key = hash_class(long_to_bytes(s)).digest()

        m = calculate_m(
            hash_class,
            n_prime,
            g,
            self.username,
            salt,
            self.public_key,
            pub_b,
            self.session_key,
        )
        if not m:
            return None

        self.h_amk = calculate_h_amk(
            hash_class, self.public_key, m, self.session_key
        )

        return m

    def verify_session(self, host_hamk: bytes) -> None:
        """Verify server's H(A, M, K) value.

        Args:
            host_hamk: Server's H(A, M, K) value.
        """
        if self.h_amk is not None and hmac.compare_digest(host_hamk, self.h_amk):
            self._authenticated = True
