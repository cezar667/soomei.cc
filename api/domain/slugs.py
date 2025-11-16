"""Domain helpers for slug validation and lookups."""
from __future__ import annotations

import re
from typing import Mapping, Any

SLUG_PATTERN = re.compile(r"[a-z0-9-]{3,30}")
RESERVED_SLUGS = {
    "onboard",
    "login",
    "auth",
    "q",
    "v",
    "u",
    "static",
    "blocked",
    "edit",
    "hooks",
    "slug",
}


def is_valid_slug(value: str | None) -> bool:
    """Return True when slug matches allowed pattern and is not reserved."""
    if not value:
        return False
    return bool(SLUG_PATTERN.fullmatch(value)) and value not in RESERVED_SLUGS


def slug_in_use(db: Mapping[str, Any], value: str | None) -> bool:
    """Check if any card already owns the provided vanity slug."""
    if not value:
        return False
    cards = db.get("cards") if isinstance(db, dict) else None
    if not isinstance(cards, dict):
        return False
    for _uid, card in cards.items():
        if isinstance(card, dict) and card.get("vanity") == value:
            return True
    return False
