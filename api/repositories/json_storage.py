"""
Current JSON-based persistence adapter.

In this initial refactor we expose the same helpers used em app.py para que
serviços/routers possam compartilhar o acesso ao arquivo JSON.
"""

from __future__ import annotations

from pathlib import Path
import json

DATA_FILE = Path(__file__).resolve().parents[1] / "data.json"


def load() -> dict:
    if DATA_FILE.exists():
        with DATA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "users": {},
        "cards": {},
        "profiles": {},
        "sessions": {},
        "verify_tokens": {},
        "custom_domains": {},
        "reset_tokens": {},
    }


def save(db: dict) -> None:
    DATA_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def db_defaults(db: dict) -> dict:
    db.setdefault("users", {})
    db.setdefault("cards", {})
    db.setdefault("profiles", {})
    db.setdefault("sessions", {})
    db.setdefault("verify_tokens", {})
    db.setdefault("custom_domains", {})
    db.setdefault("reset_tokens", {})
    return db
