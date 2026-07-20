"""Access + refresh tokens.

Access token: short-lived (10 min) HS256 JWT — stateless, checked without a DB
hit. Refresh token: an opaque 32-byte random (NOT a JWT), stored only as a
sha256 hash. Rotation with reuse detection: each refresh revokes the presented
token and mints a new one in the same `family_id`; presenting an ALREADY-REVOKED
token means it leaked, so the whole family is revoked and the user must re-login.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError, ErrorCode
from ..models import RefreshToken, User


@dataclass
class AccessClaims:
    user_id: uuid.UUID
    family_id: uuid.UUID


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_access_token(user_id: uuid.UUID, family_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "sid": str(family_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> AccessClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise AppError(ErrorCode.E_AUTH_EXPIRED, log_detail="access token expired") from exc
    except jwt.PyJWTError as exc:
        raise AppError(ErrorCode.E_AUTH_INVALID, log_detail=f"bad access token: {exc}") from exc
    try:
        return AccessClaims(user_id=uuid.UUID(payload["sub"]), family_id=uuid.UUID(payload["sid"]))
    except (KeyError, ValueError) as exc:
        raise AppError(ErrorCode.E_AUTH_INVALID, log_detail="malformed claims") from exc


def issue_refresh_token(
    db: Session, user_id: uuid.UUID, family_id: uuid.UUID, *, ua: str | None, ip: str | None
) -> str:
    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    row = RefreshToken(
        user_id=user_id,
        family_id=family_id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=(ua or "")[:255] or None,
        ip=(ip or "")[:64] or None,
    )
    db.add(row)
    db.commit()
    return raw


def revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> None:
    """Invalidate every browser session before issuing a password-change session."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()


def start_session(db: Session, user_id: uuid.UUID, *, ua: str | None, ip: str | None) -> tuple[str, str]:
    """Begin a fresh session (login/register/oauth): a new family + first pair."""
    family_id = uuid.uuid4()
    access = issue_access_token(user_id, family_id)
    refresh = issue_refresh_token(db, user_id, family_id, ua=ua, ip=ip)
    return access, refresh


def rotate_refresh_token(
    db: Session, raw_token: str, *, ua: str | None, ip: str | None
) -> tuple[str, str, User]:
    """Rotate a refresh token. Raises E_SESSION_REVOKED on reuse/unknown token,
    E_AUTH_EXPIRED on an expired one."""
    token_hash = _hash_token(raw_token)
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is None:
        raise AppError(ErrorCode.E_SESSION_REVOKED, log_detail="unknown refresh token")
    if row.revoked_at is not None:
        # Reuse of a revoked token -> the token leaked. Nuke the whole family.
        revoke_family(db, row.family_id)
        raise AppError(ErrorCode.E_SESSION_REVOKED, log_detail="refresh reuse detected")
    now = datetime.now(timezone.utc)
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise AppError(ErrorCode.E_AUTH_EXPIRED, log_detail="refresh token expired")

    user = db.get(User, row.user_id)
    if user is None:
        raise AppError(ErrorCode.E_SESSION_REVOKED, log_detail="user gone")

    # Revoke the presented token, mint a new one in the same family.
    row.revoked_at = now
    db.commit()
    access = issue_access_token(user.id, row.family_id)
    refresh = issue_refresh_token(db, user.id, row.family_id, ua=ua, ip=ip)
    return access, refresh, user
