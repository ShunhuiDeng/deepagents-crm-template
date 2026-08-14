"""Password and opaque-session-token security primitives."""

from __future__ import annotations

import hashlib
import secrets

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESSION_TOKEN_BYTES = 32

_password_hasher = PasswordHasher()


async def hash_password(password: str) -> str:
    """Hash a password with Argon2 without blocking the event loop."""
    if not password:
        raise ValueError("password must not be empty")
    return await anyio.to_thread.run_sync(_password_hasher.hash, password)


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


async def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against an Argon2 hash off the event loop."""
    if not password or not password_hash:
        return False
    return await anyio.to_thread.run_sync(_verify_password, password, password_hash)


def generate_session_token() -> str:
    """Return a URL-safe opaque token backed by 32 random bytes."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def digest_session_token(token: str) -> str:
    """Return the SHA-256 hex digest that should be persisted for a token."""
    if not token:
        raise ValueError("session token must not be empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
