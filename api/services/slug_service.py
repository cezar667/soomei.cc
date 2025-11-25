"""Slug-related use cases (validation, assignment)."""

from __future__ import annotations

from api.domain.slugs import is_valid_slug
from api.repositories.sql_repository import SQLRepository


class SlugError(Exception):
    """Base exception for slug workflow."""


class InvalidSlugError(SlugError):
    """Raised when value does not satisfy format/rules."""


class SlugUnavailableError(SlugError):
    """Raised when slug is already taken by another card."""


class CardNotFoundError(SlugError):
    """Raised when trying to update a slug for a non-existent card."""


class SlugService:
    """Provides slug availability checks and assignment helpers."""

    def __init__(self) -> None:
        self.repository = SQLRepository()

    def normalize(self, value: str | None) -> str:
        return (value or "").strip()

    def is_available(self, value: str | None) -> bool:
        candidate = self.normalize(value)
        if not is_valid_slug(candidate):
            return False
        # Prefer SQL as fonte de verdade
        if self.repository.slug_exists(candidate):
            return False
        return True

    def assign_slug(self, uid: str, slug: str) -> str:
        candidate = self.normalize(slug)
        if not is_valid_slug(candidate):
            raise InvalidSlugError("Slug invalido")
        entity = self.repository.get_card_by_uid(uid)
        if not entity:
            raise CardNotFoundError(f"Card {uid} not found")
        current = (entity.vanity or "").strip()
        if candidate == current:
            return candidate
        if self.repository.slug_exists(candidate):
            raise SlugUnavailableError("Slug indisponivel")
        self.repository.update_card_slug(uid, candidate)
        return candidate
