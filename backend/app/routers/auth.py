"""Identity routes: local/Google activation, sessions, password changes, and account view."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr, Field
from starlette.requests import Request
from starlette.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import google
from ..auth.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies, verify_csrf
from ..auth.deps import current_user, optional_user
from ..auth.passwords import (
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
    verify_password_or_dummy,
)
from ..auth.tokens import revoke_all_for_user, rotate_refresh_token, start_session
from ..auth.verification import discard_registration_code, issue_registration_code, verify_registration_code
from ..config import get_settings
from ..database import get_db
from ..delivery.email import send_verification_email
from ..errors import AppError, ErrorCode
from ..middleware import get_request_id
from ..models import OAuthAccount, RefreshToken, User
from ..rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
_logger = logging.getLogger("app")
_settings = get_settings()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class VerifyEmailBody(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendVerificationBody(BaseModel):
    email: EmailStr


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    confirm_new_password: str = Field(min_length=1, max_length=200)


class VerificationView(BaseModel):
    verification_required: bool = True
    email: str


class UserView(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    email_verified: bool
    memory_opt_in: bool
    completion_email_opt_in: bool
    location_share_opt_in: bool
    has_password: bool
    google_linked: bool
    telegram_linked: bool
    avatar_url: str | None


def _client(request: Request) -> tuple[str | None, str | None]:
    ua = request.headers.get("User-Agent")
    ip = request.client.host if request.client else None
    return ua, ip


def _avatar_url(user: User) -> str | None:
    if user.avatar_key:
        version = user.avatar_updated_at.isoformat() if user.avatar_updated_at else "0"
        return f"/me/avatar?v={quote(version)}"
    if user.avatar_source == "google" and user.google_avatar_url:
        return user.google_avatar_url
    return None


def _google_profile_value(value: object, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:max_length] or None


def _apply_google_profile(user: User, *, display_name: str | None, picture: str | None) -> None:
    # Preserve a name the user chose locally, but fill an empty profile with
    # the Google Account name. Uploaded avatars always take precedence.
    if display_name and user.display_name is None:
        user.display_name = display_name
    if picture and not user.avatar_key:
        user.avatar_source, user.google_avatar_url = "google", picture


def _view(user: User, db: Session) -> UserView:
    google_linked = db.scalar(
        select(OAuthAccount.id).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google")
    ) is not None
    return UserView(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        email_verified=user.email_verified,
        memory_opt_in=user.memory_opt_in,
        completion_email_opt_in=user.completion_email_opt_in,
        location_share_opt_in=user.location_share_opt_in,
        has_password=user.password_hash is not None,
        google_linked=google_linked,
        telegram_linked=user.telegram_chat_id is not None,
        avatar_url=_avatar_url(user),
    )


def _send_activation_code(db: Session, user: User, *, enforce_cooldown: bool) -> None:
    code = issue_registration_code(db, user, enforce_cooldown=enforce_cooldown)
    try:
        send_verification_email(to_email=user.email, code=code)
    except Exception:
        # A provider failure must not impose resend cooldown on an OTP the user
        # never received. The code was committed before the outbound request,
        # so explicitly retire it before surfacing the safe delivery error.
        try:
            discard_registration_code(db, user, code)
        except Exception:  # noqa: BLE001 - preserve the original delivery error
            _logger.exception("could not discard undelivered activation code")
        raise


@router.post("/register", response_model=VerificationView)
def register(body: RegisterBody, db: Session = Depends(get_db)) -> VerificationView:
    email = body.email.lower().strip()
    validate_password_policy(body.password)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.email_verified:
            raise AppError(ErrorCode.E_EMAIL_TAKEN, log_detail="duplicate verified email")
        # A pre-existing pending registration should resume activation rather
        # than create a second account. It never gets a session here.
        _send_activation_code(db, user, enforce_cooldown=True)
        return VerificationView(email=user.email)
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    _send_activation_code(db, user, enforce_cooldown=False)
    return VerificationView(email=user.email)


@router.post("/verify-email", response_model=UserView)
def verify_email(body: VerifyEmailBody, request: Request, response: Response, db: Session = Depends(get_db)) -> UserView:
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if user is None:
        raise AppError(ErrorCode.E_VERIFICATION_INVALID, log_detail="verification user missing")
    verify_registration_code(db, user, body.code)
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return _view(user, db)


@router.post("/verify-email/resend", response_model=VerificationView)
def resend_verification(body: ResendVerificationBody, db: Session = Depends(get_db)) -> VerificationView:
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if user is None or user.email_verified:
        raise AppError(ErrorCode.E_VERIFICATION_INVALID, log_detail="verification resend user unavailable")
    _send_activation_code(db, user, enforce_cooldown=True)
    return VerificationView(email=user.email)


@router.post("/login", response_model=UserView)
@limiter.limit(_settings.rate_limit_login)
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> UserView:
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    # Always pays the same Argon2id cost whether or not `user`/its password
    # hash exists, so response timing can't be used to enumerate accounts.
    if not verify_password_or_dummy(body.password, user.password_hash if user else None):
        raise AppError(ErrorCode.E_AUTH_INVALID, log_detail="login failed")
    if not user.email_verified:
        raise AppError(ErrorCode.E_EMAIL_UNVERIFIED, log_detail="unverified login")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return _view(user, db)


@router.post("/password", response_model=UserView)
def change_password(
    body: ChangePasswordBody,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> UserView:
    verify_csrf(request)
    if user.password_hash is None:
        raise AppError(ErrorCode.E_FORBIDDEN, log_detail="password change for oauth-only account")
    if body.new_password != body.confirm_new_password:
        raise AppError(ErrorCode.E_VALIDATION, log_detail="new password confirmation mismatch")
    if body.new_password == body.current_password:
        raise AppError(ErrorCode.E_PASSWORD_POLICY, log_detail="password reuse")
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(ErrorCode.E_AUTH_INVALID, log_detail="password change current mismatch")
    validate_password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    revoke_all_for_user(db, user.id)
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return _view(user, db)


@router.post("/refresh", response_model=UserView)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> UserView:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise AppError(ErrorCode.E_AUTH_REQUIRED, log_detail="no refresh cookie")
    ua, ip = _client(request)
    access, new_refresh, user = rotate_refresh_token(db, raw, ua=ua, ip=ip)
    if not user.email_verified:
        raise AppError(ErrorCode.E_EMAIL_UNVERIFIED, log_detail="unverified refresh")
    set_auth_cookies(response, access, new_refresh)
    return _view(user, db)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        from ..auth.tokens import _hash_token, revoke_family
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw)))
        if row is not None:
            revoke_family(db, row.family_id)
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserView)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    return _view(user, db)


_STATE_COOKIE = "ac_oauth_state"
_VERIFIER_COOKIE = "ac_oauth_verifier"
_INTENT_COOKIE = "ac_oauth_intent"


def _oauth_cookie_path() -> str:
    # Scope the state/PKCE cookies to whatever path the browser will actually
    # request for the callback — derived from the configured redirect URI
    # rather than hardcoded, since that path is "/auth/google" direct-to-backend
    # locally but "/api/auth/google" behind a reverse proxy that strips an
    # /api/ prefix (see DEPLOYMENT.md §7.3). A mismatch here means the browser
    # silently never sends the cookies back on the callback request.
    callback_path = urlparse(get_settings().google_oauth_redirect_uri).path
    return callback_path.rsplit("/", 1)[0] or "/"


@router.get("/google/start")
def google_start(request: Request, intent: str = "login", db: Session = Depends(get_db)) -> RedirectResponse:
    if not google.is_configured():
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail="google oauth not configured")
    intent = "link" if intent == "link" else "login"
    if intent == "link" and optional_user(request, db) is None:
        raise AppError(ErrorCode.E_AUTH_REQUIRED, log_detail="link intent without session")
    settings = get_settings()
    state = uuid.uuid4().hex
    verifier, challenge = google.make_pkce()
    redirect = RedirectResponse(google.authorization_url(state, challenge), status_code=302)
    common = dict(httponly=True, secure=settings.cookies_secure, samesite="lax", max_age=600, path=_oauth_cookie_path())
    redirect.set_cookie(_STATE_COOKIE, state, **common)
    redirect.set_cookie(_VERIFIER_COOKIE, verifier, **common)
    redirect.set_cookie(_INTENT_COOKIE, intent, **common)
    return redirect


@router.get("/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    settings = get_settings()

    def _clear(response: RedirectResponse) -> RedirectResponse:
        cookie_path = _oauth_cookie_path()
        response.delete_cookie(_STATE_COOKIE, path=cookie_path)
        response.delete_cookie(_VERIFIER_COOKIE, path=cookie_path)
        response.delete_cookie(_INTENT_COOKIE, path=cookie_path)
        return response

    def _err(reason: str = "1", log_detail: str | None = None) -> RedirectResponse:
        if log_detail:
            _logger.warning("google oauth callback failed [req=%s]: %s", get_request_id(), log_detail)
        return _clear(RedirectResponse(f"{settings.frontend_url}/?auth_error={reason}", status_code=302))

    intent = request.cookies.get(_INTENT_COOKIE) or "login"
    code, state = request.query_params.get("code"), request.query_params.get("state")
    cookie_state, verifier = request.cookies.get(_STATE_COOKIE), request.cookies.get(_VERIFIER_COOKIE)
    if not code or not state or not cookie_state or state != cookie_state or not verifier:
        return _err(log_detail="missing/mismatched oauth parameters")
    try:
        tokens = google.exchange_code(code, verifier)
        claims = google.verify_id_token(tokens["id_token"])
        profile = google.fetch_user_profile(tokens["access_token"])
    except AppError as exc:
        return _err(log_detail=f"{exc.code.value}: {exc.log_detail}")
    except Exception as exc:  # noqa: BLE001
        return _err(log_detail=f"unexpected: {exc!r}")

    provider_account_id = claims.get("sub")
    email = (claims.get("email") or "").lower().strip()
    if not provider_account_id or not email or not bool(claims.get("email_verified")):
        return _err(log_detail="google identity has no verified email")
    if profile.get("sub") != provider_account_id:
        return _err(log_detail="userinfo subject does not match id token")
    profile_email = _google_profile_value(profile.get("email"), 320)
    if profile_email and profile_email.lower() != email:
        return _err(log_detail="userinfo email does not match id token")
    display_name = _google_profile_value(profile.get("name"), 120) or _google_profile_value(claims.get("name"), 120)
    picture = _google_profile_value(profile.get("picture"), 2048) or _google_profile_value(claims.get("picture"), 2048)
    existing_link = db.scalar(select(OAuthAccount).where(OAuthAccount.provider == "google", OAuthAccount.provider_account_id == provider_account_id))

    if intent == "link":
        current = optional_user(request, db)
        if current is None:
            return _err()
        if existing_link is not None and existing_link.user_id != current.id:
            return _err("oauth_taken")
        if existing_link is None:
            db.add(OAuthAccount(user_id=current.id, provider="google", provider_account_id=provider_account_id))
        _apply_google_profile(current, display_name=display_name, picture=picture)
        db.commit()
        return _clear(RedirectResponse(f"{settings.frontend_url}/?linked=google", status_code=302))

    user = db.get(User, existing_link.user_id) if existing_link is not None else None
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, email_verified=False)
            db.add(user)
            db.flush()
        _apply_google_profile(user, display_name=display_name, picture=picture)
        db.add(OAuthAccount(user_id=user.id, provider="google", provider_account_id=provider_account_id))
        db.commit()
    else:
        _apply_google_profile(user, display_name=display_name, picture=picture)

    if not user.email_verified:
        try:
            _send_activation_code(db, user, enforce_cooldown=False)
        except Exception as exc:  # noqa: BLE001
            return _err(log_detail=f"could not send activation code: {exc!r}")
        return _clear(RedirectResponse(f"{settings.frontend_url}/?verify_email={quote(user.email)}", status_code=302))

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    redirect = _clear(RedirectResponse(f"{settings.frontend_url}/", status_code=302))
    set_auth_cookies(redirect, access, refresh)
    return redirect


@router.post("/google/unlink", response_model=UserView)
def google_unlink(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    if user.password_hash is None:
        raise AppError(ErrorCode.E_OAUTH_UNLINK_BLOCKED, log_detail="no password set on account")
    db.execute(OAuthAccount.__table__.delete().where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google"))
    db.commit()
    return _view(user, db)
