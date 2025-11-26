"""
Card-related helpers (lookups, mutations) shared across routers/services.
"""

from __future__ import annotations

from typing import Tuple

from api.repositories.sql_repository import SQLRepository
from api.db.models import Card

_repo = SQLRepository()


def _entity_to_card_dict(entity: Card) -> dict:
    return {
        "uid": entity.uid,
        "status": entity.status,
        "pin": entity.pin,
        "billing_status": entity.billing_status,
        "user": entity.owner_email or "",
        "vanity": entity.vanity or "",
        "metrics": {"views": int(entity.metrics_views or 0)},
        "custom_domain": entity.custom_domain_meta or {},
    }


def find_card_by_slug(slug: str) -> Tuple[dict, str | None, dict | None]:
    """
    Locate a card by vanity slug or UID. Returns (db, uid, card).
    """
    slug_value = (slug or "").strip()
    entity = _repo.get_card_by_vanity(slug_value) or _repo.get_card_by_uid(slug_value)
    if entity:
        card = _entity_to_card_dict(entity)
        return {}, entity.uid, card
    return {}, None, None
