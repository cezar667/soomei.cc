"""One-off migration script: JSON (data.json) -> Postgres."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from datetime import datetime, timezone

# Garantir que o pacote api seja importável quando rodado diretamente
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.repositories.sql_repository import SQLRepository
from api.db.session import get_session
from api.db.models import VerifyToken, ResetToken, UserSession, AdminSession, CustomDomain


def _to_datetime(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Arquivo nao encontrado: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # defaults
    data.setdefault("users", {})
    data.setdefault("cards", {})
    data.setdefault("profiles", {})
    data.setdefault("sessions", {})
    data.setdefault("verify_tokens", {})
    data.setdefault("reset_tokens", {})
    data.setdefault("sessions_admin", {})
    data.setdefault("custom_domains", {})
    return data


def migrate() -> None:
    repo = SQLRepository()
    data_file = Path(__file__).resolve().parents[1] / "api" / "data.json"
    db = _load_json(data_file)

    users = db.get("users", {})
    for email, meta in users.items():
        repo.upsert_user(
            email,
            password_hash=meta.get("pwd") or "",
            email_verified_at=_to_datetime(meta.get("email_verified_at")),
        )

    cards = db.get("cards", {})
    for uid, card_meta in cards.items():
        repo.sync_card_from_json(uid, card_meta)

    profiles = db.get("profiles", {})
    for email, profile_meta in profiles.items():
        repo.upsert_profile(email, profile_meta)

    custom_domains = db.get("custom_domains", {})
    for host, meta in custom_domains.items():
        uid = meta.get("uid")
        if uid:
            repo.register_custom_domain(host, uid)

    with get_session() as session:
        for token, meta in (db.get("verify_tokens", {}) or {}).items():
            entity = VerifyToken(token=token, email=meta.get("email") or "", created_at=_to_datetime(meta.get("created_at")) or datetime.now(timezone.utc))
            session.merge(entity)

        for token, meta in (db.get("reset_tokens", {}) or {}).items():
            entity = ResetToken(token=token, email=meta.get("email") or "", created_at=_to_datetime(meta.get("created_at")) or datetime.now(timezone.utc))
            session.merge(entity)

        for token, meta in (db.get("sessions", {}) or {}).items():
            entity = UserSession(
                token=token,
                user_email=meta.get("email") or "",
                expires_at=_to_datetime(meta.get("exp")) or datetime.now(timezone.utc),
            )
            session.merge(entity)

        for token, meta in (db.get("sessions_admin", {}) or {}).items():
            entity = AdminSession(
                token=token,
                email=meta.get("email") or "",
                csrf_token=meta.get("csrf") or "",
                expires_at=_to_datetime(meta.get("exp")) or datetime.now(timezone.utc),
            )
            session.merge(entity)

        session.commit()


if __name__ == "__main__":
    migrate()
    print("JSON data migrated to Postgres successfully.")
