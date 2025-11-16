"""Session helpers (issue tokens, resolve current user)."""
from __future__ import annotations

import secrets
import time

from fastapi import Request

from api.repositories.json_storage import load, save, db_defaults


def issue_session(db: dict, email: str) -> str:
    """Create a new session token and persist it in the JSON store."""
    token = secrets.token_urlsafe(32)
    db.setdefault("sessions", {})[token] = {"email": email, "ts": int(time.time())}
    save(db)
    return token


def current_user_email(request: Request) -> str | None:
    """Return the e-mail associated with the current session cookie, if any."""
    token = request.cookies.get("session")
    if not token:
        return None
    db = db_defaults(load())
    session = db.get("sessions", {}).get(token)
    if not session:
        return None
    return session.get("email")
