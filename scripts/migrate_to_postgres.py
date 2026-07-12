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
from api.db.create_tables import create_all
from api.db.session import get_engine, get_session
from api.db.models import VerifyToken, ResetToken, UserSession, AdminSession, CustomDomain
from sqlalchemy import text


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


def _split_sql_statements(sql: str) -> list[str]:
    """Split the repo migration SQL into simple executable statements."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return [statement for statement in statements if statement.strip()]


def _ensure_current_schema() -> None:
    """
    Ensure the current SQLAlchemy tables and incremental Postgres upgrades exist.

    Base.metadata.create_all() creates missing tables, but it does not alter existing
    tables. The membership webhook feature added columns to cards, so the legacy
    JSON migration must apply that incremental SQL before loading Card rows.
    """
    create_all()
    migration_path = ROOT / "db" / "migrations" / "20260712_membership_webhooks.sql"
    if not migration_path.exists():
        return
    sql = migration_path.read_text(encoding="utf-8")
    engine = get_engine()
    with engine.begin() as conn:
        for statement in _split_sql_statements(sql):
            conn.execute(text(statement))


def migrate() -> None:
    _ensure_current_schema()
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
