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
import os
from collections.abc import Callable
from typing import Any

from ..utils import bytes_to_long, long_to_bytes

SHA1 = 0
SHA224 = 1
SHA256 = 2
SHA384 = 3
SHA512 = 4

NG_1024 = 0
NG_2048 = 1
NG_3072 = 2
NG_4096 = 3
NG_8192 = 4

_hash_map = {
    SHA1: hashlib.sha1,
    SHA224: hashlib.sha224,
    SHA256: hashlib.sha256,
    SHA384: hashlib.sha384,
    SHA512: hashlib.sha512,
}


_ng_const = (
    # 1024-bit
    (
        """\
EEAF0AB9ADB38DD69C33F80AFA8FC5E86072618775FF3C0B9EA2314C9C256576D674DF7496\
EA81D3383B4813D692C6E0E0D5D8E250B98BE48E495C1D6089DAD15DC7D7B46154D6B6CE8E\
F4AD69B15D4982559B297BCF1885C529F566660E57EC68EDBC3C05726CC02FD4CBF4976EAA\
9AFD5138FE8376435B9FC61D2FC0EB06E3""",
        "2",
    ),
    # 2048
    (
        """\
AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050A37329CBB4\
A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50E8083969EDB767B0CF60\
95179A163AB3661A05FBD5FAAAE82918A9962F0B93B855F97993EC975EEAA80D740ADBF4FF\
747359D041D5C33EA71D281E446B14773BCA97B43A23FB801676BD207A436C6481F1D2B907\
8717461A5B9D32E688F87748544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB37861\
60279004E57AE6AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DB\
FBB694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73""",
        "2",
    ),
    # 3072
    (
        """\
FFFFFFFFFFFFFFFFC90FDAA22168C2\
34C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E\
3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B5\
76625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE\
9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D3\
9A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED5290770\
96966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E77\
2C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF69558171839\
95497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A\
33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6\
E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA\
06D98A0864D87602733EC86A64521F2B18177B200CBBE117577A615D6C77\
0988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2\
CAFFFFFFFFFFFFFFFF""",
        "5",
    ),
    # 4096
    (
        """\
FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E08\
8A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B\
302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9\
A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE6\
49286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8\
FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D\
670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C\
180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718\
3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D\
04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7D\
B3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D226\
1AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C\
BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFC\
E0FD108E4B82D120A92108011A723C12A787E6D788719A10BDBA5B26\
99C327186AF4E23C1A946834B6150BDA2583E9CA2AD44CE8DBBBC2DB\
04DE8EF92E8EFC141FBECAA6287C59474E6BC05D99B2964FA090C3A2\
233BA186515BE7ED1F612970CEE2D7AFB81BDD762170481CD0069127\
D5B05AA993B4EA988D8FDDC186FFB7DC90A6C08F4DF435C934063199\
FFFFFFFFFFFFFFFF""",
        "5",
    ),
    # 8192
    (
        """\
FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E08\
8A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B\
302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9\
A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE6\
49286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8\
FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D\
670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C\
180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718\
3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D\
04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7D\
B3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D226\
1AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C\
BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFC\
E0FD108E4B82D120A92108011A723C12A787E6D788719A10BDBA5B26\
99C327186AF4E23C1A946834B6150BDA2583E9CA2AD44CE8DBBBC2DB\
04DE8EF92E8EFC141FBECAA6287C59474E6BC05D99B2964FA090C3A2\
233BA186515BE7ED1F612970CEE2D7AFB81BDD762170481CD0069127\
D5B05AA993B4EA988D8FDDC186FFB7DC90A6C08F4DF435C934028492\
36C3FAB4D27C7026C1D4DCB2602646DEC9751E763DBA37BDF8FF9406\
AD9E530EE5DB382F413001AEB06A53ED9027D831179727B0865A8918\
DA3EDBEBCF9B14ED44CE6CBACED4BB1BDB7F1447E6CC254B33205151\
2BD7AF426FB8F401378CD2BF5983CA01C64B92ECF032EA15D1721D03\
F482D7CE6E74FEF6D55E702F46980C82B5A84031900B1C9E59E7C97F\
BEC7E8F323A97A7E36CC88BE0F1D45B7FF585AC54BD407B22B4154AA\
CC8F6D7EBF48E1D814CC5ED20F8037E0A79715EEF29BE32806A1D58B\
B7C5DA76F550AA3D8A1FBFF0EB19CCB1A313D55CDA56C9EC2EF29632\
387FE8D76E3C0468043E8F663F4860EE12BF2D5B0B7474D6E694F91E\
6DBE115974A3926F12FEE5E438777CB6A932DF8CD8BEC4D073B931BA\
3BC832B68D9DD300741FA7BF8AFC47ED2576F6936BA424663AAB639C\
5AE4F5683423B4742BF1C978238F16CBE39D652DE3FDB8BEFC848AD9\
22222E04A4037C0713EB57A81A23F0C73473FC646CEA306B4BCBC886\
2F8385DDFA9D4B7FA2C087E879683303ED5BDD3A062B3CF5B3A278A6\
6D2A13F83F44F82DDF310EE074AB6A364597E899A0255DC164F31CC5\
0846851DF9AB48195DED7EA1B1D510BD7EE74D73FAF36BC31ECFA268\
359046F4EB879F924009438B481C6CD7889A002ED5EE382BC9190DA6\
FC026E479558E4475677E9AA9E3050E2765694DFC81F56E880B96E71\
60C980DD98EDD3DFFFFFFFFFFFFFFFFF""",
        "0x13",
    ),
)


def get_ng(ng_type: int) -> tuple[int, int]:
    """Get N and g values for the specified group type.

    Args:
        ng_type: Group type constant (NG_1024, NG_2048, etc.).

    Returns:
        Tuple of (N, g) as integers.
    """
    n_hex, g_hex = _ng_const[ng_type]
    return int(n_hex, 16), int(g_hex, 16)


def get_random(nbytes: int) -> Any:
    """Get random integer with specified number of bytes.

    Args:
        nbytes: Number of random bytes.

    Returns:
        Random integer.
    """
    return bytes_to_long(os.urandom(nbytes))


def get_random_of_length(nbytes: int) -> Any:
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


def calculate_x(hash_class: Callable, salt: Any, username: str, password: str) -> int:
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


def generate_salt_and_verifier(
    username: str,
    password: str,
    *,
    len_s: int,
    hash_alg: int = SHA512,
    ng_type: int = NG_3072,
) -> tuple[bytes, bytes]:
    """Generate salt and password verifier for SRP-6a.

    Args:
        username: Username.
        password: Password.
        len_s: Length of salt in bytes.
        hash_alg: Hash algorithm constant.
        ng_type: Group type constant.

    Returns:
        Tuple of (salt, verifier) as bytes.
    """
    hash_class = _hash_map[hash_alg]
    n_prime, g = get_ng(ng_type)

    salt = long_to_bytes(get_random(len_s))
    verifier = long_to_bytes(
        pow(g, calculate_x(hash_class, salt, username, password), n_prime)
    )

    return salt, verifier


def calculate_m(
    hash_class: Callable,
    n_prime: int,
    g: int,
    username: str,
    salt: int,
    pub_a: int,
    pub_b: int,
    session_key: bytes,
) -> Any:
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
) -> Any:
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
        ng_type: int = NG_3072,
    ):
        """Initialize SRP-6a client.

        Args:
            username: Username for authentication.
            password: Password for authentication.
            hash_alg: Hash algorithm constant.
            ng_type: Group type constant.
        """
        hash_class = _hash_map[hash_alg]

        n_prime, g = get_ng(ng_type)
        k = hash_args(hash_class, n_prime, g, width=len(long_to_bytes(n_prime)))

        self.username = username
        self.password = password

        self.private_key = get_random_of_length(32)
        self.public_key = pow(g, self.private_key, n_prime)

        self.verifier: int | None = None
        self.session_key: bytes | None = None
        self.h_amk = None
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

    def get_username(self) -> str:
        """Get username.

        Returns:
            Username string.
        """
        return self.username

    def get_ephemeral_secret(self) -> Any:
        """Get ephemeral secret as bytes.

        Returns:
            Private key as bytes.
        """
        return long_to_bytes(self.private_key)

    def get_session_key(self) -> Any:
        """Get session key if authenticated.

        Returns:
            Session key or None if not authenticated.
        """
        return self.session_key if self._authenticated else None

    def start_authentication(self) -> tuple[str, bytes]:
        """Start authentication and get initial message.

        Returns:
            Tuple of (username, public_key_bytes).
        """
        return (self.username, long_to_bytes(self.public_key))

    def process_challenge(self, bytes_s: bytes, bytes_b: bytes) -> Any:
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
        if host_hamk == self.h_amk:
            self._authenticated = True


class AuthenticationFailed(Exception):
    """Exception raised when SRP-6a authentication fails."""
