"""
Security helpers (hashing, password verification, etc.).
"""

import hashlib


def hash_password(password: str) -> str:
    """Hashes the password using scrypt (same config as legacy `h`)."""
    return hashlib.scrypt(password.encode(), salt=b"soomei", n=2**14, r=8, p=1).hex()

