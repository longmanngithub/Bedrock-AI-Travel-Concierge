"""Authenticated account preferences, avatar lifecycle, and Telegram linking."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..auth.cookies import verify_csrf
from ..auth.deps import current_user
from ..avatar_storage import MAX_AVATAR_BYTES, get_avatar_storage, store_avatar
from ..database import get_db
from ..delivery.telegram import get_bot_username, is_configured as telegram_configured
from ..errors import AppError, ErrorCode
from ..models import User, UserPreference
from ..queue import keys
from ..queue.settings import sync_redis
from .auth import UserView, _view

router = APIRouter(prefix="/me", tags=["settings"])


class MemoryOptInBody(BaseModel):
    opt_in: bool


class CompletionEmailBody(BaseModel):
    opt_in: bool


class LocationSharingBody(BaseModel):
    opt_in: bool


class TelegramLinkView(BaseModel):
    code: str
    deep_link: str | None
    expires_in: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.patch("/memory", response_model=UserView)
def set_memory_opt_in(body: MemoryOptInBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    user.memory_opt_in = body.opt_in
    db.commit()
    db.refresh(user)
    return _view(user, db)


@router.patch("/completion-email", response_model=UserView)
def set_completion_email_opt_in(body: CompletionEmailBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    user.completion_email_opt_in = body.opt_in
    db.commit()
    db.refresh(user)
    return _view(user, db)


@router.patch("/location-sharing", response_model=UserView)
def set_location_sharing_opt_in(body: LocationSharingBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    user.location_share_opt_in = body.opt_in
    db.commit()
    db.refresh(user)
    return _view(user, db)


@router.delete("/memory", response_model=UserView)
def delete_memory(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    db.execute(delete(UserPreference).where(UserPreference.user_id == user.id))
    db.commit()
    return _view(user, db)


@router.post("/avatar", response_model=UserView)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> UserView:
    verify_csrf(request)
    if user.password_hash is None:
        raise AppError(ErrorCode.E_FORBIDDEN, log_detail="avatar upload for oauth-only user")
    raw = await file.read(MAX_AVATAR_BYTES + 1)
    # Pillow decode/re-encode is CPU-bound; this route is async so FastAPI
    # won't thread-offload it automatically the way it does for a `def`
    # route — do it explicitly or it blocks the single event loop this
    # process shares with every other user's chat SSE stream.
    new_key = await run_in_threadpool(store_avatar, raw)
    old_key = user.avatar_key
    user.avatar_key = new_key
    user.avatar_source = "local"
    user.avatar_updated_at = _now()
    db.commit()
    if old_key and old_key != new_key:
        await run_in_threadpool(get_avatar_storage().delete, old_key)
    return _view(user, db)


@router.get("/avatar")
def get_avatar(user: User = Depends(current_user)) -> Response:
    if not user.avatar_key:
        raise AppError(ErrorCode.E_NOT_FOUND, log_detail="no local avatar")
    return Response(
        content=get_avatar_storage().get(user.avatar_key),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/avatar", response_model=UserView)
def delete_avatar(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    if user.password_hash is None:
        raise AppError(ErrorCode.E_FORBIDDEN, log_detail="avatar delete for oauth-only user")
    old_key = user.avatar_key
    user.avatar_key = None
    user.avatar_source = "google" if user.google_avatar_url else None
    user.avatar_updated_at = _now()
    db.commit()
    if old_key:
        get_avatar_storage().delete(old_key)
    return _view(user, db)


@router.post("/telegram/link-code", response_model=TelegramLinkView)
def create_telegram_link_code(request: Request, user: User = Depends(current_user)) -> TelegramLinkView:
    verify_csrf(request)
    code = secrets.token_urlsafe(9)
    r = sync_redis()
    try:
        r.set(keys.telegram_link_key(code), str(user.id), ex=keys.TELEGRAM_LINK_TTL)
    finally:
        r.close()
    username = get_bot_username() if telegram_configured() else None
    deep_link = f"https://t.me/{username}?start={code}" if username else None
    return TelegramLinkView(code=code, deep_link=deep_link, expires_in=keys.TELEGRAM_LINK_TTL)


@router.post("/telegram/unlink", response_model=UserView)
def unlink_telegram(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    user.telegram_chat_id = None
    db.commit()
    db.refresh(user)
    return _view(user, db)
