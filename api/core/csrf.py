from __future__ import annotations

import secrets
from urllib import parse as urlparse

from fastapi import HTTPException, Request, Response

from api.core.config import get_settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_csrf_token(request: Request) -> str:
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
    header_token = request.headers.get(CSRF_HEADER_NAME)
    token = (supplied_token or "").strip() or (header_token or "").strip()
    if not cookie_token or not token:
        raise HTTPException(403, "CSRF token ausente.")
    if not secrets.compare_digest(cookie_token, token):
        raise HTTPException(403, "CSRF token invalido.")
    _validate_origin(request)
