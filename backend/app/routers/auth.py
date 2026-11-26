"""Identity routes: local/Google activation, sessions, password changes, and account view."""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr, Field
from starlette.requests import Request
from starlette.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import google
from ..auth.cookies import (
    ACCESS_COOKIE,
    clear_auth_cookies,
    refresh_cookie_candidates,
    set_auth_cookies,
    verify_csrf,
)
from ..auth.deps import current_user, optional_user
from ..auth.passwords import (
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
    verify_password_or_dummy,
)
from ..auth.tokens import (
    _hash_token,
    decode_access_token,
    revoke_family,
    pick_live_refresh_token,
    revoke_all_for_user,
    rotate_refresh_token,
    start_session,
)
from ..auth.verification import discard_registration_code, issue_registration_code, verify_registration_code
from ..config import get_settings
from ..database import get_db
from ..delivery.email import send_verification_email
from ..errors import AppError, ErrorCode
from ..middleware import get_request_id
from ..models import OAuthAccount, RefreshToken, User
from ..rate_limit import account_key, limiter

router = APIRouter(prefix="/auth", tags=["auth"])
_logger = logging.getLogger("app")
_settings = get_settings()


class RegisterBody(BaseModel):
    email: EmailStr


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class VerifyEmailBody(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=1, max_length=200)


class GoogleLinkBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


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
@limiter.limit(_settings.rate_limit_login)
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)) -> VerificationView:
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.email_verified:
            raise AppError(ErrorCode.E_EMAIL_TAKEN, log_detail="duplicate verified email")
        # A pre-existing pending registration should resume activation rather
        # than create a second account. It never gets a session here.
        _send_activation_code(db, user, enforce_cooldown=True)
        return VerificationView(email=user.email)
    user = User(email=email)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            raise
    _send_activation_code(db, user, enforce_cooldown=True)
    return VerificationView(email=user.email)


@router.post("/verify-email", response_model=UserView)
@limiter.limit(_settings.rate_limit_login)
def verify_email(body: VerifyEmailBody, request: Request, response: Response, db: Session = Depends(get_db)) -> UserView:
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if user is None:
        raise AppError(ErrorCode.E_VERIFICATION_INVALID, log_detail="verification user missing")
    validate_password_policy(body.password)
    verify_registration_code(db, user, body.code, body.password)
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return _view(user, db)


@router.post("/verify-email/resend", response_model=VerificationView)
@limiter.limit(_settings.rate_limit_login)
def resend_verification(body: ResendVerificationBody, request: Request, db: Session = Depends(get_db)) -> VerificationView:
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
    if user is not None:
        db.refresh(user, with_for_update=True)
    # Always pays the same Argon2id cost whether or not `user`/its password
    # hash exists, so response timing can't be used to enumerate accounts.
    if not verify_password_or_dummy(body.password, user.password_hash if user else None):
        raise AppError(ErrorCode.E_AUTH_INVALID, log_detail="login failed")
    if not user.email_verified:
        raise AppError(ErrorCode.E_EMAIL_UNVERIFIED, log_detail="unverified login")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(timezone.utc)
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
    db.refresh(user, with_for_update=True)
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
    revoke_all_for_user(db, user.id)
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return _view(user, db)


@router.post("/refresh", response_model=UserView)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    # May legitimately be more than one during the legacy "/auth"-path cookie
    # migration; pick the live one rather than letting header order decide.
    raw = pick_live_refresh_token(db, refresh_cookie_candidates(request))
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
    verify_csrf(request)

    # Revoke every family the presented cookies point at, not just whichever
    # one header order happened to surface — during the legacy-path migration
    # a stale duplicate would otherwise leave the live session alive after a
    # logout the user explicitly asked for.
    families = set()
    access = request.cookies.get(ACCESS_COOKIE)
    if access:
        try:
            families.add(decode_access_token(access).family_id)
        except AppError:
            pass
    for raw in refresh_cookie_candidates(request):
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw)))
        if row is not None:
            families.add(row.family_id)
    for family in families:
        revoke_family(db, family)
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserView)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    return _view(user, db)


_STATE_COOKIE = "ac_oauth_state"
_VERIFIER_COOKIE = "ac_oauth_verifier"
_INTENT_COOKIE = "ac_oauth_intent"
_LINK_SESSION_COOKIE = "ac_oauth_link_session"


def _oauth_cookie_path() -> str:
    # Scope the state/PKCE cookies to whatever path the browser will actually
    # request for the callback — derived from the configured redirect URI
    # rather than hardcoded, since that path is "/auth/google" direct-to-backend
    # locally but "/api/auth/google" behind a reverse proxy that strips an
    # /api/ prefix (see DEPLOYMENT.md §7.3). A mismatch here means the browser
    # silently never sends the cookies back on the callback request.
    callback_path = urlparse(get_settings().google_oauth_redirect_uri).path
    return callback_path.rsplit("/", 1)[0] or "/"


def _set_oauth_flow(response: Response, request: Request, *, link: bool) -> str:
    if not google.is_configured():
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail="google oauth not configured")
    state = uuid.uuid4().hex
    verifier, challenge = google.make_pkce()
    common = dict(
        httponly=True, secure=get_settings().cookies_secure, samesite="lax",
        max_age=600, path=_oauth_cookie_path(),
    )
    response.set_cookie(_STATE_COOKIE, state, **common)
    response.set_cookie(_VERIFIER_COOKIE, verifier, **common)
    response.set_cookie(_INTENT_COOKIE, "link" if link else "login", **common)
    if link:
        claims = decode_access_token(request.cookies[ACCESS_COOKIE])
        response.set_cookie(_LINK_SESSION_COOKIE, str(claims.family_id), **common)
    return google.authorization_url(state, challenge)


@router.get("/google/start")
def google_start(request: Request) -> RedirectResponse:
    response = RedirectResponse("/", status_code=302)
    response.headers["location"] = _set_oauth_flow(response, request, link=False)
    return response


@router.post("/google/link")
@limiter.limit(_settings.rate_limit_login, key_func=account_key)
def google_link(
    body: GoogleLinkBody, request: Request, response: Response,
    user: User = Depends(current_user),
) -> dict[str, str]:
    verify_csrf(request)
    if not verify_password_or_dummy(body.password, user.password_hash):
        raise AppError(ErrorCode.E_AUTH_INVALID, log_detail="Google link password mismatch")
    return {"url": _set_oauth_flow(response, request, link=True)}


@router.get("/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    settings = get_settings()

    def _clear(response: RedirectResponse) -> RedirectResponse:
        cookie_path = _oauth_cookie_path()
        response.delete_cookie(_STATE_COOKIE, path=cookie_path)
        response.delete_cookie(_VERIFIER_COOKIE, path=cookie_path)
        response.delete_cookie(_INTENT_COOKIE, path=cookie_path)
        response.delete_cookie(_LINK_SESSION_COOKIE, path=cookie_path)
        return response

    def _err(reason: str = "1", log_detail: str | None = None) -> RedirectResponse:
        if log_detail:
            _logger.warning("google oauth callback failed [req=%s]: %s", get_request_id(), log_detail)
        return _clear(RedirectResponse(f"{settings.frontend_url}/?auth_error={reason}", status_code=302))

    intent = request.cookies.get(_INTENT_COOKIE) or "login"
    code, state = request.query_params.get("code"), request.query_params.get("state")
    cookie_state, verifier = request.cookies.get(_STATE_COOKIE), request.cookies.get(_VERIFIER_COOKIE)
    if not code or not state or not cookie_state or not secrets.compare_digest(state.encode(), cookie_state.encode()) or not verifier:
        return _err(log_detail="missing/mismatched oauth parameters")
    try:
        tokens = google.exchange_code(code, verifier)
        claims = google.verify_id_token(tokens["id_token"])
        profile = google.fetch_user_profile(tokens["access_token"])
    except AppError as exc:
        return _err(log_detail=f"{exc.code.value}: {exc.log_detail}")
    except Exception as exc:  # noqa: BLE001
        return _err(log_detail=f"unexpected OAuth error ({type(exc).__name__})")

    provider_account_id = claims.get("sub")
    email = (claims.get("email") or "").lower().strip()
    if not provider_account_id or not email or claims.get("email_verified") is not True:
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
        claims = decode_access_token(request.cookies[ACCESS_COOKIE])
        if request.cookies.get(_LINK_SESSION_COOKIE) != str(claims.family_id):
            return _err()
        db.refresh(current, with_for_update=True)
        if optional_user(request, db) is None:
            return _err()
        if existing_link is not None and existing_link.user_id != current.id:
            return _err("oauth_taken")
        if existing_link is None:
            if db.scalar(select(OAuthAccount).where(
                OAuthAccount.user_id == current.id, OAuthAccount.provider == "google"
            )) is not None:
                return _err("oauth_taken")
            db.add(OAuthAccount(user_id=current.id, provider="google", provider_account_id=provider_account_id))
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                return _err("oauth_taken")
        _apply_google_profile(current, display_name=display_name, picture=picture)
        db.commit()
        return _clear(RedirectResponse(f"{settings.frontend_url}/?linked=google", status_code=302))

    user = db.get(User, existing_link.user_id) if existing_link is not None else None
    if user is not None:
        db.refresh(user, with_for_update=True)
        # A concurrent disconnect can invalidate a mapping we read before the lock.
        if not user.email_verified or db.get(OAuthAccount, existing_link.id, populate_existing=True) is None:
            return _err("oauth_link_required")
    else:
        hosted_domain = claims.get("hd")
        authoritative_email = email.endswith("@gmail.com") or (
            isinstance(hosted_domain, str) and bool(hosted_domain)
        )
        if db.scalar(select(User.id).where(User.email == email)) is not None or not authoritative_email:
            return _err("oauth_link_required")
        user = User(email=email, email_verified=True)
        db.add(user)
        try:
            db.flush()
            db.add(OAuthAccount(user_id=user.id, provider="google", provider_account_id=provider_account_id))
            db.flush()
        except IntegrityError:
            db.rollback()
            return _err("oauth_link_required")
    _apply_google_profile(user, display_name=display_name, picture=picture)

    user.last_login_at = datetime.now(timezone.utc)
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    redirect = _clear(RedirectResponse(f"{settings.frontend_url}/", status_code=302))
    set_auth_cookies(redirect, access, refresh)
    return redirect


@router.post("/google/unlink", response_model=UserView)
def google_unlink(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserView:
    verify_csrf(request)
    if user.password_hash is None:
        raise AppError(ErrorCode.E_OAUTH_UNLINK_BLOCKED, log_detail="no password set on account")
    db.refresh(user, with_for_update=True)
    db.execute(delete(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google"))
    revoke_all_for_user(db, user.id)
    ua, ip = _client(request)
    access, refresh = start_session(db, user.id, ua=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return _view(user, db)
