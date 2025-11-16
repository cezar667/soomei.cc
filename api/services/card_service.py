"""
Card-related helpers (lookups, mutations) shared across routers/services.
"""

from __future__ import annotations

from typing import Tuple, Any

from api.repositories.json_storage import load, db_defaults


def find_card_by_slug(slug: str) -> Tuple[dict, str | None, dict | None]:
    """
    Locate a card by vanity slug or UID. Returns (db, uid, card).
    """
    db = db_defaults(load())
    for uid, card in db.get("cards", {}).items():
        if card.get("vanity") == slug:
            return db, uid, card
    if slug in db.get("cards", {}):
        return db, slug, db["cards"][slug]
    return db, None, None
