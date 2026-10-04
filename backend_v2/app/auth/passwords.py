"""
Password hashing (BLUEPRINT.md §11 "Authentication ... with password
hashing"). scrypt from the standard library -- a memory-hard KDF -- with a
random 16-byte salt per password. Stored as
    scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>
so the cost parameters travel with the hash and can be raised later
without invalidating existing passwords. Verification is constant-time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2**14, 8, 1, 32
MIN_PASSWORD_LENGTH = 12


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


# Verified against when the username doesn't exist, so a login attempt takes
# the same time whether or not the account exists (no user enumeration by timing).
DUMMY_HASH = f"scrypt${N}${R}${P}${_b64(b'0' * 16)}${_b64(b'0' * DKLEN)}"
