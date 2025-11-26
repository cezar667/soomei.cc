"""Custom domain helpers and service layer."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from api.repositories.json_storage import save
from api.services.card_service import find_card_by_slug
from api.services.domain_service import (
    CUSTOM_DOMAIN_STATUS_ACTIVE,
    CUSTOM_DOMAIN_STATUS_DISABLED,
    CUSTOM_DOMAIN_STATUS_PENDING,
    CUSTOM_DOMAIN_STATUS_REJECTED,
    active_custom_domain_host,
    custom_domain_conflict,
    ensure_custom_domain_meta,
    find_card_by_custom_domain,
    is_valid_custom_domain,
    normalize_domain_name,
    unregister_custom_domain,
)


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
            raise CustomDomainError("Dominio invalido. Use apenas letras, numeros e pontos.", "invalid_domain")
        if custom_domain_conflict(db, normalized, exclude_uid=uid):
            raise CustomDomainError("Este dominio ja esta em uso.", "in_use", 409)
        meta = ensure_custom_domain_meta(card)
        now = int(time.time())
        meta.update(
            {
                "requested_host": normalized,
                "status": CUSTOM_DOMAIN_STATUS_PENDING,
                "requested_at": now,
                "requested_by": owner,
                "admin_note": "",
                "updated_at": now,
            }
        )
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
            raise CustomDomainError("Nenhuma solicitacao pendente.", "not_pending")
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
