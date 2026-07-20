"""One-time email activation-code issuance and verification."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError, ErrorCode
from ..models import EmailVerification, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(user_id, code: str) -> str:
    settings = get_settings()
    secret = settings.email_verification_secret or settings.jwt_secret
    return hashlib.sha256(f"{user_id}:{code}:{secret}".encode()).hexdigest()


def issue_registration_code(db: Session, user: User, *, enforce_cooldown: bool = True) -> str:
    """Invalidate previous active codes and create one short-lived OTP.

    The plaintext value is returned only to the trusted mail sender; it is never
    stored or logged.
    """
    settings = get_settings()
    latest = db.scalar(
        select(EmailVerification)
        .where(
            EmailVerification.user_id == user.id,
            EmailVerification.purpose == "registration",
            EmailVerification.consumed_at.is_(None),
        )
        .order_by(EmailVerification.sent_at.desc())
    )
    now = _now()
    if latest is not None:
        sent_at = latest.sent_at if latest.sent_at.tzinfo else latest.sent_at.replace(tzinfo=timezone.utc)
        if enforce_cooldown and (now - sent_at).total_seconds() < settings.otp_resend_cooldown_seconds:
            raise AppError(ErrorCode.E_VERIFICATION_COOLDOWN, log_detail="OTP resend cooldown")
        latest.consumed_at = now

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        EmailVerification(
            user_id=user.id,
            purpose="registration",
            code_hash=_digest(user.id, code),
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    db.commit()
    return code


def discard_registration_code(db: Session, user: User, code: str) -> None:
    """Invalidate a code whose outbound email could not be delivered.

    Issuance is committed before sending so the plaintext OTP is never held in
    a long transaction. If delivery fails, discarding that active row lets the
    user immediately retry instead of being blocked by resend cooldown for a
    code they never received.
    """
    row = db.scalar(
        select(EmailVerification).where(
            EmailVerification.user_id == user.id,
            EmailVerification.purpose == "registration",
            EmailVerification.code_hash == _digest(user.id, code),
            EmailVerification.consumed_at.is_(None),
        )
    )
    if row is not None:
        row.consumed_at = _now()
        db.commit()


def verify_registration_code(db: Session, user: User, code: str) -> None:
    settings = get_settings()
    row = db.scalar(
        select(EmailVerification)
        .where(
            EmailVerification.user_id == user.id,
            EmailVerification.purpose == "registration",
            EmailVerification.consumed_at.is_(None),
        )
        .order_by(EmailVerification.sent_at.desc())
    )
    if row is None:
        raise AppError(ErrorCode.E_VERIFICATION_INVALID, log_detail="no active OTP")
    now = _now()
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise AppError(ErrorCode.E_VERIFICATION_EXPIRED, log_detail="OTP expired")
    if row.locked_at is not None or row.attempts >= settings.otp_max_attempts:
        raise AppError(ErrorCode.E_VERIFICATION_LOCKED, log_detail="OTP attempts exhausted")
    if not (code.isdigit() and len(code) == 6 and secrets.compare_digest(row.code_hash, _digest(user.id, code))):
        row.attempts += 1
        if row.attempts >= settings.otp_max_attempts:
            row.locked_at = now
        db.commit()
        raise AppError(
            ErrorCode.E_VERIFICATION_LOCKED if row.locked_at else ErrorCode.E_VERIFICATION_INVALID,
            log_detail="invalid OTP",
        )
    row.consumed_at = now
    user.email_verified = True
    db.commit()
