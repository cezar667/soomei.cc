"""Security helpers (hashing and verification)."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher, exceptions as argon_exc

_ph = PasswordHasher()
_LEGACY_SALT = b"soomei"
_PREFIX = "argon2$"


def hash_password(password: str) -> str:
    """Create a modern Argon2 hash with a prefix for detection."""
    hashed = _ph.hash(password)
    return f"{_PREFIX}{hashed}"


def _legacy_hash(password: str) -> str:
    return hashlib.scrypt(password.encode(), salt=_LEGACY_SALT, n=2**14, r=8, p=1).hex()


def verify_password(password: str, stored_hash: str | None) -> bool:
    stored = stored_hash or ""
    if stored.startswith(_PREFIX):
        hashed = stored[len(_PREFIX) :]
        try:
            return _ph.verify(hashed, password)
        except (argon_exc.VerifyMismatchError, argon_exc.VerificationError):
            return False
    legacy = _legacy_hash(password)
    return secrets.compare_digest(legacy, stored)

