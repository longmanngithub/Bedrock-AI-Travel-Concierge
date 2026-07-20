"""Google OAuth — Authorization Code flow with PKCE.

`state` and the PKCE `code_verifier` are carried in short-lived signed cookies
(one fewer moving part than a Redis round-trip). The returned `id_token` is
verified against Google's JWKS. We only trust the email when `email_verified` is
true, and we NEVER auto-link an unverified Google email to an existing password
account (that would be an account-takeover primitive).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import ssl

import certifi
import httpx
import jwt
from jwt import PyJWKClient

from ..config import get_settings
from ..errors import AppError, ErrorCode

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

# PyJWKClient fetches the JWKS via stdlib `urllib`, which — unlike httpx/requests
# — trusts the OS certificate store rather than bundling `certifi`. On macOS
# Python.org builds that store is often empty/unwired, which fails as
# `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` even
# though the JWKS endpoint itself is fine. Pin the client to certifi's bundle
# explicitly so this doesn't depend on the deployment environment's OS trust
# store being configured correctly.
_jwk_client = PyJWKClient(_JWKS_URI, ssl_context=ssl.create_default_context(cafile=certifi.where()))


def is_configured() -> bool:
    s = get_settings()
    return bool(s.google_oauth_client_id and s.google_oauth_client_secret)


def make_pkce() -> tuple[str, str]:
    """Return (verifier, challenge)."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(state: str, code_challenge: str) -> str:
    s = get_settings()
    from urllib.parse import urlencode

    params = {
        "client_id": s.google_oauth_client_id,
        "redirect_uri": s.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{_AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(code: str, code_verifier: str) -> dict:
    s = get_settings()
    try:
        resp = httpx.post(
            _TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": s.google_oauth_client_id,
                "client_secret": s.google_oauth_client_secret,
                "redirect_uri": s.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        # Google's actual reason (redirect_uri_mismatch, invalid_client,
        # invalid_grant, ...) is in the response body, not the status line —
        # `str(exc)` alone is useless for diagnosing a misconfigured client.
        raise AppError(
            ErrorCode.E_OAUTH_FAILED,
            log_detail=f"token exchange failed: HTTP {exc.response.status_code} body={exc.response.text[:500]}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail=f"token exchange failed: {exc!r}") from exc


def fetch_user_profile(access_token: str) -> dict[str, object]:
    """Fetch the consented Google Account profile for an OAuth access token."""
    try:
        response = httpx.get(
            _USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        response.raise_for_status()
        profile = response.json()
    except httpx.HTTPStatusError as exc:
        raise AppError(
            ErrorCode.E_OAUTH_FAILED,
            log_detail=f"userinfo fetch failed: HTTP {exc.response.status_code} body={exc.response.text[:500]}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail=f"userinfo fetch failed: {exc!r}") from exc
    if not isinstance(profile, dict):
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail="userinfo response was not an object")
    return profile


def verify_id_token(id_token: str) -> dict:
    """Verify signature + issuer + audience. Returns the claims dict."""
    s = get_settings()
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=s.google_oauth_client_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail=f"id_token invalid: {exc}") from exc
    if claims.get("iss") not in _ISSUERS:
        raise AppError(ErrorCode.E_OAUTH_FAILED, log_detail="bad issuer")
    return claims
