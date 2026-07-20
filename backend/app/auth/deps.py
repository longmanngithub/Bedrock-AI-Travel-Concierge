"""FastAPI auth dependencies.

`current_user` requires a valid access-token cookie (401 E_AUTH_REQUIRED /
E_AUTH_EXPIRED otherwise). `optional_user` returns None instead of raising, for
routes that allow anonymous use (the landing chat) but branch on identity.
"""
from __future__ import annotations

from fastapi import Depends
from starlette.requests import Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError, ErrorCode
from ..models import User
from .cookies import ACCESS_COOKIE
from .tokens import decode_access_token


def _user_from_request(request: Request, db: Session) -> User | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return None
    claims = decode_access_token(token)  # may raise E_AUTH_EXPIRED / E_AUTH_INVALID
    return db.get(User, claims.user_id)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _user_from_request(request, db)
    if user is None:
        raise AppError(ErrorCode.E_AUTH_REQUIRED, log_detail="no session")
    if not user.email_verified:
        raise AppError(ErrorCode.E_EMAIL_UNVERIFIED, log_detail="unverified session")
    return user


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return _user_from_request(request, db)
    except AppError:
        # An expired/invalid token on an anonymous-allowed route -> treat as
        # anonymous rather than failing the request.
        return None
