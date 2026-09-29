"""Password and opaque token primitives used by Agora authentication."""

from __future__ import annotations

import hashlib
import hmac
import secrets


PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024

_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_LENGTH = 16
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_HASH_PREFIX = "scrypt"


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise ValueError("Password must be text.")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"Password must be between {PASSWORD_MIN_LENGTH} and {PASSWORD_MAX_LENGTH} characters."
        )
    encoded = password.encode("utf-8")
    if len(encoded) > PASSWORD_MAX_LENGTH * 4:
        raise ValueError("Password is too long.")
    return encoded


def hash_password(password: str) -> str:
    """Hash a password with stdlib scrypt and a fresh random salt."""
    password_data = _password_bytes(password)
    salt = secrets.token_bytes(_SCRYPT_SALT_LENGTH)
    digest = hashlib.scrypt(
        password_data,
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"{_HASH_PREFIX}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        f"{salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a stored scrypt hash; malformed or unsupported hashes fail closed."""
    if not isinstance(stored_hash, str) or not isinstance(password, str):
        return False
    try:
        scheme, n_text, r_text, p_text, salt_hex, digest_hex = stored_hash.split("$")
        if (
            scheme != _HASH_PREFIX
            or int(n_text) != _SCRYPT_N
            or int(r_text) != _SCRYPT_R
            or int(p_text) != _SCRYPT_P
        ):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) != _SCRYPT_SALT_LENGTH or len(expected) != _SCRYPT_DKLEN:
            return False
        password_data = password.encode("utf-8")
        if len(password_data) > PASSWORD_MAX_LENGTH * 4:
            return False
        actual = hashlib.scrypt(
            password_data,
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=_SCRYPT_DKLEN,
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, OverflowError, MemoryError):
        return False


def new_session_token() -> str:
    """Return a high-entropy bearer token for the HttpOnly session cookie."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Return the lowercase SHA-256 hex digest stored in Oracle."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    """Return a high-entropy token sent only to the signed-in browser client."""
    return secrets.token_urlsafe(32)
