from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib import parse as urlparse

from fastapi import HTTPException, Request, Response

from api.core.config import get_settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
SESSION_COOKIE_NAME = "session"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _session_token(session_token: str) -> str:
    """Build a stable CSRF token without exposing the bearer session token."""
    return hmac.new(
        session_token.encode("utf-8"),
        b"soomei-csrf-v1",
        hashlib.sha256,
    ).hexdigest()


def ensure_csrf_token(request: Request) -> str:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        return _session_token(session_token)
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token or len(token) < 16:
        token = _new_token()
    return token


def set_csrf_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    secure_cookie = settings.app_env == "prod"
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=7 * 24 * 60 * 60,
        httponly=False,
        secure=secure_cookie,
        samesite="strict",
        path="/",
    )


def _validate_origin(request: Request) -> None:
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""
    source = origin or referer
    if not source:
        return
    try:
        parsed = urlparse.urlparse(source)
    except ValueError:
        raise HTTPException(403, "Origem invalida.")
    host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    parsed_host = (parsed.hostname or "").lower()
    if parsed_host and host and parsed_host != host:
        raise HTTPException(403, "Origem invalida.")
    if parsed.scheme and parsed.scheme != request.url.scheme:
        raise HTTPException(403, "Origem invalida.")


def validate_csrf(request: Request, supplied_token: str | None) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    query_token = request.query_params.get(CSRF_COOKIE_NAME)
    token = (
        (supplied_token or "").strip()
        or (header_token or "").strip()
        or (query_token or "").strip()
    )
    if not token:
        raise HTTPException(403, "Token CSRF do formulario ausente.")
    expected_token = _session_token(session_token) if session_token else cookie_token
    if not expected_token:
        raise HTTPException(403, "Cookie de sessao/CSRF ausente.")
    if not secrets.compare_digest(expected_token, token):
        raise HTTPException(403, "CSRF token invalido.")
    _validate_origin(request)
