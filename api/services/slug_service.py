"""Slug-related use cases (validation, assignment)."""

from __future__ import annotations

from dataclasses import dataclass

from api.domain.slugs import is_valid_slug, slug_in_use
from api.repositories.json_storage import db_defaults, load, save


class SlugError(Exception):
    """Base exception for slug workflow."""


class InvalidSlugError(SlugError):
    """Raised when value does not satisfy format/rules."""


class SlugUnavailableError(SlugError):
    """Raised when slug is already taken by another card."""


class CardNotFoundError(SlugError):
    """Raised when trying to update a slug for a non-existent card."""


@dataclass
class SlugService:
    """Provides slug availability checks and assignment helpers."""

    def normalize(self, value: str | None) -> str:
        return (value or "").strip()

    def is_available(self, value: str | None) -> bool:
        candidate = self.normalize(value)
        if not is_valid_slug(candidate):
            return False
        db = db_defaults(load())
        return not slug_in_use(db, candidate)

    def assign_slug(self, uid: str, slug: str) -> str:
        candidate = self.normalize(slug)
        if not is_valid_slug(candidate):
            raise InvalidSlugError("Slug invalido")
        db = db_defaults(load())
        card = db.get("cards", {}).get(uid)
        if not card:
            raise CardNotFoundError(f"Card {uid} not found")
        current = (card.get("vanity") or "").strip()
        if candidate == current:
            return candidate
        if slug_in_use(db, candidate):
            raise SlugUnavailableError("Slug indisponivel")
        card["vanity"] = candidate
        db["cards"][uid] = card
        save(db)
        return candidate
