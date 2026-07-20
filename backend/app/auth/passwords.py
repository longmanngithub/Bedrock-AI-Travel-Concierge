"""Argon2id hashing and local-password policy."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from ..errors import AppError, ErrorCode

_ph = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=1)


def validate_password_policy(password: str) -> None:
    """Enforce a predictable, user-visible local-account password policy."""
    if len(password) < 12 or len(password) > 200:
        raise AppError(ErrorCode.E_PASSWORD_POLICY, log_detail="password length invalid")
    if not any(c.islower() for c in password):
        raise AppError(ErrorCode.E_PASSWORD_POLICY, log_detail="password missing lowercase")
    if not any(c.isupper() for c in password):
        raise AppError(ErrorCode.E_PASSWORD_POLICY, log_detail="password missing uppercase")
    if not any(c.isdigit() for c in password):
        raise AppError(ErrorCode.E_PASSWORD_POLICY, log_detail="password missing digit")
    if not any(not c.isalnum() and not c.isspace() for c in password):
        raise AppError(ErrorCode.E_PASSWORD_POLICY, log_detail="password missing symbol")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


# Hashed once at import time so a login attempt against a nonexistent (or
# password-less, Google-only) account still pays the same Argon2id cost as a
# real wrong-password attempt — otherwise the two cases are distinguishable
# by response latency, turning /auth/login into an email-enumeration oracle.
_DUMMY_HASH = _ph.hash("not-a-real-account-timing-equalization-only")


def verify_password_or_dummy(password: str, hashed: str | None) -> bool:
    if hashed is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, hashed)


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except Exception:  # noqa: BLE001
        return False
