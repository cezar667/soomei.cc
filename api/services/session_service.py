"""Session helpers (issue tokens, cookies, validation)."""
from __future__ import annotations

import secrets
import time

from fastapi import Request, Response

from api.core.config import get_settings
from api.repositories.json_storage import load, save, db_defaults

SESSION_COOKIE_NAME = "session"


def issue_session(db: dict, email: str) -> str:
    """Create a new session token and persist it in the JSON store."""
    token = secrets.token_urlsafe(32)
    settings = get_settings()
    expires_at = int(time.time()) + max(60, settings.session_ttl_seconds)
    db.setdefault("sessions", {})[token] = {"email": email, "exp": expires_at}
    save(db)
    return token


def current_user_email(request: Request) -> str | None:
    """Return the e-mail associated with the current session cookie, if any."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    db = db_defaults(load())
    session = db.get("sessions", {}).get(token)
    if not session:
        return None
    exp = int(session.get("exp") or 0)
    if exp and exp < time.time():
        db["sessions"].pop(token, None)
        try:
            save(db)
        except Exception:
            pass
        return None
    return session.get("email")


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    secure_cookie = settings.app_env == "prod"
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=secure_cookie,
        samesite="strict",
        max_age=settings.session_ttl_seconds,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
