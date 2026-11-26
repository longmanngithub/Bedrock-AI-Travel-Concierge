"""FastAPI auth dependencies.

`current_user` requires a valid access-token cookie (401 E_AUTH_REQUIRED /
E_AUTH_EXPIRED otherwise). `optional_user` returns None instead of raising, for
routes that allow anonymous use (the landing chat) but branch on identity.
"""
from __future__ import annotations

from fastapi import Depends
from starlette.requests import Request
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..errors import AppError, ErrorCode
from ..models import User
from .cookies import ACCESS_COOKIE
from .tokens import decode_access_token, family_is_active


def _user_from_request(request: Request, db: Session) -> User | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return None
    claims = decode_access_token(token)  # may raise E_AUTH_EXPIRED / E_AUTH_INVALID
    if not family_is_active(db, claims.user_id, claims.family_id):
        raise AppError(ErrorCode.E_SESSION_REVOKED, log_detail="access family revoked")
    return db.get(User, claims.user_id)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _user_from_request(request, db)
    if user is None:
        raise AppError(ErrorCode.E_AUTH_REQUIRED, log_detail="no session")
    if not user.email_verified:
        raise AppError(ErrorCode.E_EMAIL_UNVERIFIED, log_detail="unverified session")
    expected = request.headers.get("X-Expected-Account")
    if expected is not None and expected != str(user.id):
        raise AppError(ErrorCode.E_FORBIDDEN, log_detail="account changed before the request")
    request.state.account_id = user.id
    return user


def session_token_is_active(token: str) -> bool:
    try:
        claims = decode_access_token(token)
    except AppError:
        return False
    with SessionLocal() as db:
        return family_is_active(db, claims.user_id, claims.family_id)


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return _user_from_request(request, db)
    except AppError:
        # An expired/invalid token on an anonymous-allowed route -> treat as
        # anonymous rather than failing the request.
        return None
