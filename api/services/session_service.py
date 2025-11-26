"""Session helpers (issue tokens, cookies, validation)."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from api.core.config import get_settings
from api.db.models import UserSession
from api.db.session import get_session

SESSION_COOKIE_NAME = "session"


def issue_session(email: str) -> str:
    """Create a new session token and persist it in the SQL store (optionally JSON fallback)."""
    token = secrets.token_urlsafe(32)
    settings = get_settings()
    ttl = max(60, settings.session_ttl_seconds)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    with get_session() as session:
        session.add(UserSession(token=token, user_email=email, expires_at=expires_at))
        session.commit()
    return token


def current_user_email(request: Request) -> str | None:
    """Return the e-mail associated with the current session cookie, if any."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    now = datetime.now(timezone.utc)
    with get_session() as session:
        db_session = session.get(UserSession, token)
        if db_session:
            if db_session.expires_at and db_session.expires_at < now:
                session.delete(db_session)
                session.commit()
                return None
            return db_session.user_email

    return None


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


def delete_session(token: str) -> None:
    """Remove a session token from persistent stores."""
    if not token:
        return
    with get_session() as session:
        entity = session.get(UserSession, token)
        if entity:
            session.delete(entity)
            session.commit()
