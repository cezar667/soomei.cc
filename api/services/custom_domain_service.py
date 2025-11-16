"""Custom domain helpers and service layer."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from api.repositories.json_storage import load, save, db_defaults
from api.services.card_service import find_card_by_slug

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

CUSTOM_DOMAIN_STATUS_PENDING = "pending"
CUSTOM_DOMAIN_STATUS_ACTIVE = "active"
CUSTOM_DOMAIN_STATUS_REJECTED = "rejected"
CUSTOM_DOMAIN_STATUS_DISABLED = "disabled"


class CustomDomainError(Exception):
    def __init__(self, message: str, code: str = "invalid", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


@dataclass
class CustomDomainState:
    status: str
    requested_host: str
    active_host: str


def normalize_domain_name(value: str) -> str:
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


def is_valid_custom_domain(value: str) -> bool:
    host = normalize_domain_name(value)
    if not host:
        return False
    return bool(DOMAIN_RE.match(host))


def active_custom_domain_host(card: dict | None) -> str:
    meta = card.get("custom_domain") if isinstance(card, dict) else None
    if not isinstance(meta, dict):
        return ""
    return normalize_domain_name(meta.get("active_host", ""))


def requested_custom_domain_host(card: dict | None) -> str:
    meta = card.get("custom_domain") if isinstance(card, dict) else None
    if not isinstance(meta, dict):
        return ""
    return normalize_domain_name(meta.get("requested_host", ""))


def ensure_custom_domain_meta(card: dict) -> dict:
    meta = card.get("custom_domain")
    if not isinstance(meta, dict):
        meta = {}
        card["custom_domain"] = meta
    return meta


def register_custom_domain(db: dict, uid: str, host: str) -> None:
    host_norm = normalize_domain_name(host)
    if not host_norm:
        return
    db.setdefault("custom_domains", {})[host_norm] = {"uid": uid}


def unregister_custom_domain(db: dict, host: str) -> None:
    host_norm = normalize_domain_name(host)
    if not host_norm:
        return
    db.setdefault("custom_domains", {}).pop(host_norm, None)


def custom_domain_conflict(db: dict, host: str, exclude_uid: Optional[str] = None) -> bool:
    host_norm = normalize_domain_name(host)
    if not host_norm:
        return False
    entry = db.get("custom_domains", {}).get(host_norm)
    if entry and entry.get("uid") != exclude_uid:
        return True
    for uid, card in db.get("cards", {}).items():
        if uid == exclude_uid:
            continue
        meta = card.get("custom_domain") or {}
        active_host = normalize_domain_name(meta.get("active_host", ""))
        requested_host = normalize_domain_name(meta.get("requested_host", ""))
        status = (meta.get("status") or "").lower()
        if active_host == host_norm:
            return True
        if requested_host == host_norm and status in (CUSTOM_DOMAIN_STATUS_PENDING, CUSTOM_DOMAIN_STATUS_ACTIVE):
            return True
    return False


def find_card_by_custom_domain(host: str) -> Tuple[dict, Optional[str], Optional[dict]]:
    db = db_defaults(load())
    host_norm = normalize_domain_name(host)
    if not host_norm:
        return db, None, None
    entry = db.get("custom_domains", {}).get(host_norm)
    if not entry:
        for uid, card in db.get("cards", {}).items():
            meta = card.get("custom_domain") or {}
            if normalize_domain_name(meta.get("active_host", "")) == host_norm:
                db.setdefault("custom_domains", {})[host_norm] = {"uid": uid}
                save(db)
                return db, uid, card
        return db, None, None
    uid = entry.get("uid")
    card = db.get("cards", {}).get(uid)
    if not card:
        db.get("custom_domains", {}).pop(host_norm, None)
        save(db)
        return db, None, None
    return db, uid, card


class CustomDomainService:
    def request(self, slug: str, requester: Optional[str], host: str) -> CustomDomainState:
        db, uid, card = find_card_by_slug(slug)
        if not card:
            raise CustomDomainError("Cartao nao encontrado", "not_found", 404)
        owner = card.get("user") or ""
        if not requester or requester != owner:
            raise CustomDomainError("Acesso negado.", "unauthorized", 403)
        normalized = normalize_domain_name(host)
        if not normalized or not is_valid_custom_domain(normalized):
            raise CustomDomainError("Dom�nio inv�lido. Use apenas letras, n�meros e pontos.", "invalid_domain")
        if custom_domain_conflict(db, normalized, exclude_uid=uid):
            raise CustomDomainError("Este dom�nio j� est� em uso.", "in_use", 409)
        meta = ensure_custom_domain_meta(card)
        meta["requested_host"] = normalized
        meta["status"] = CUSTOM_DOMAIN_STATUS_PENDING
        meta["requested_at"] = int(time.time())
        meta["requested_by"] = owner
        meta["admin_note"] = ""
        meta["updated_at"] = int(time.time())
        db["cards"][uid] = card
        save(db)
        return CustomDomainState(meta.get("status", ""), meta.get("requested_host", ""), meta.get("active_host", ""))

    def withdraw(self, slug: str, requester: Optional[str]) -> CustomDomainState:
        db, uid, card = find_card_by_slug(slug)
        if not card:
            raise CustomDomainError("Cartao nao encontrado", "not_found", 404)
        owner = card.get("user") or ""
        if not requester or requester != owner:
            raise CustomDomainError("Acesso negado.", "unauthorized", 403)
        meta = ensure_custom_domain_meta(card)
        status = (meta.get("status") or "").lower()
        if status != CUSTOM_DOMAIN_STATUS_PENDING or not meta.get("requested_host"):
            raise CustomDomainError("Nenhuma solicita��o pendente.", "not_pending")
        meta.pop("requested_host", None)
        meta["status"] = CUSTOM_DOMAIN_STATUS_ACTIVE if active_custom_domain_host(card) else CUSTOM_DOMAIN_STATUS_DISABLED
        meta["updated_at"] = int(time.time())
        db["cards"][uid] = card
        save(db)
        return CustomDomainState(meta.get("status", ""), meta.get("requested_host", ""), meta.get("active_host", ""))

    def remove(self, slug: str, requester: Optional[str]) -> CustomDomainState:
        db, uid, card = find_card_by_slug(slug)
        if not card:
            raise CustomDomainError("Cartao nao encontrado", "not_found", 404)
        owner = card.get("user") or ""
        if not requester or requester != owner:
            raise CustomDomainError("Acesso negado.", "unauthorized", 403)
        meta = ensure_custom_domain_meta(card)
        active_host = meta.get("active_host")
        if not active_host:
            raise CustomDomainError("Nenhuma URL ativa para remover.", "no_active")
        unregister_custom_domain(db, active_host)
        meta["active_host"] = ""
        meta.pop("requested_host", None)
        meta["status"] = CUSTOM_DOMAIN_STATUS_DISABLED
        meta["updated_at"] = int(time.time())
        db["cards"][uid] = card
        save(db)
        return CustomDomainState(meta.get("status", ""), meta.get("requested_host", ""), meta.get("active_host", ""))
