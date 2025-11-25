"""
Core helpers for custom domain management (normalization, lookups, registry).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from api.repositories.sql_repository import SQLRepository

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

CUSTOM_DOMAIN_STATUS_PENDING = "pending"
CUSTOM_DOMAIN_STATUS_ACTIVE = "active"
CUSTOM_DOMAIN_STATUS_REJECTED = "rejected"
CUSTOM_DOMAIN_STATUS_DISABLED = "disabled"


@dataclass
class DomainRecord:
    """Typed view returned by DomainService when looking up a host."""

    uid: Optional[str]
    card: Optional[dict]


_sql_repo = SQLRepository()


class DomainService:
    """Encapsulates custom domain normalization and persistence helpers."""

    def normalize(self, value: str | None) -> str:
        v = (value or "").strip().lower()
        if not v:
            return ""
        if "://" in v:
            v = v.split("://", 1)[1]
        for sep in ("/", "?", "#"):
            if sep in v:
                v = v.split(sep, 1)[0]
        if ":" in v:
            v = v.split(":", 1)[0]
        return v.strip().strip(".")

    def is_valid(self, value: str | None) -> bool:
        host = self.normalize(value)
        if not host:
            return False
        return bool(DOMAIN_RE.match(host))

    def ensure_meta(self, card: dict) -> dict:
        meta = card.get("custom_domain")
        if not isinstance(meta, dict):
            meta = {}
            card["custom_domain"] = meta
        return meta

    def active_host(self, card: dict | None) -> str:
        if not isinstance(card, dict):
            return ""
        meta = card.get("custom_domain") or {}
        return self.normalize(meta.get("active_host", ""))

    def requested_host(self, card: dict | None) -> str:
        if not isinstance(card, dict):
            return ""
        meta = card.get("custom_domain") or {}
        return self.normalize(meta.get("requested_host", ""))

    def register(self, db: dict, uid: str, host: str) -> None:
        host_norm = self.normalize(host)
        if not host_norm:
            return
        _sql_repo.register_custom_domain(host_norm, uid)

    def unregister(self, db: dict, host: str) -> None:
        host_norm = self.normalize(host)
        if not host_norm:
            return
        _sql_repo.unregister_custom_domain(host_norm)

    def has_conflict(self, db: dict, host: str, *, exclude_uid: Optional[str] = None) -> bool:
        host_norm = self.normalize(host)
        if not host_norm:
            return False
        entry = _sql_repo.get_custom_domain(host_norm)
        if entry and entry.card_uid != exclude_uid:
            return True
        cards = _sql_repo.get_cards_for_domain_checks()
        for card in cards:
            if exclude_uid and card.uid == exclude_uid:
                continue
            meta = card.custom_domain_meta or {}
            active_host = self.normalize(meta.get("active_host", ""))
            requested_host = self.normalize(meta.get("requested_host", ""))
            status = (meta.get("status") or "").lower()
            if active_host == host_norm:
                return True
            if requested_host == host_norm and status in (CUSTOM_DOMAIN_STATUS_PENDING, CUSTOM_DOMAIN_STATUS_ACTIVE):
                return True
        return False

    def find_card_by_host(self, host: str) -> DomainRecord:
        host_norm = self.normalize(host)
        if not host_norm:
            return DomainRecord(uid=None, card=None)
        entity = _sql_repo.get_card_by_custom_domain(host_norm)
        if entity:
            card_dict = {
                "uid": entity.uid,
                "status": entity.status,
                "pin": entity.pin,
                "billing_status": entity.billing_status,
                "user": entity.owner_email or "",
                "vanity": entity.vanity or entity.uid,
                "metrics": {"views": int(entity.metrics_views or 0)},
                "custom_domain": entity.custom_domain_meta or {},
            }
            return DomainRecord(uid=entity.uid, card=card_dict)
        return DomainRecord(uid=None, card=None)


# Singleton-style helper reused across modules
domain_service = DomainService()


def normalize_domain_name(value: str | None) -> str:
    return domain_service.normalize(value)


def is_valid_custom_domain(value: str | None) -> bool:
    return domain_service.is_valid(value)


def active_custom_domain_host(card: dict | None) -> str:
    return domain_service.active_host(card)


def requested_custom_domain_host(card: dict | None) -> str:
    return domain_service.requested_host(card)


def ensure_custom_domain_meta(card: dict) -> dict:
    return domain_service.ensure_meta(card)


def register_custom_domain(db: dict, uid: str, host: str) -> None:
    domain_service.register(db, uid, host)


def unregister_custom_domain(db: dict, host: str) -> None:
    domain_service.unregister(db, host)


def custom_domain_conflict(db: dict, host: str, exclude_uid: Optional[str] = None) -> bool:
    return domain_service.has_conflict(db, host, exclude_uid=exclude_uid)


def find_card_by_custom_domain(host: str) -> Tuple[dict, Optional[str], Optional[dict]]:
    record = domain_service.find_card_by_host(host)
    return {}, record.uid, record.card
