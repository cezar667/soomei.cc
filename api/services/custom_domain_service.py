"""Custom domain helpers and service layer."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

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
from api.repositories.sql_repository import SQLRepository


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
    def __init__(self) -> None:
        self.repository = SQLRepository()

    def request(self, slug: str, requester: Optional[str], host: str) -> CustomDomainState:
        entity = self.repository.get_card_by_vanity(slug) or self.repository.get_card_by_uid(slug)
        if not entity:
            raise CustomDomainError("Cartao nao encontrado", "not_found", 404)
        owner = (entity.owner_email or "").strip()
        if not requester or requester != owner:
            raise CustomDomainError("Acesso negado.", "unauthorized", 403)
        normalized = normalize_domain_name(host)
        if not normalized or not is_valid_custom_domain(normalized):
            raise CustomDomainError("Dominio invalido. Use apenas letras, numeros e pontos.", "invalid_domain")
        if custom_domain_conflict({}, normalized, exclude_uid=entity.uid):
            raise CustomDomainError("Este dominio ja esta em uso.", "in_use", 409)
        card_meta = {"custom_domain": entity.custom_domain_meta or {}}
        meta = ensure_custom_domain_meta(card_meta)
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
        self.repository.update_card_custom_domain_meta(entity.uid, meta)
        return CustomDomainState(meta.get("status", ""), meta.get("requested_host", ""), meta.get("active_host", ""))

    def withdraw(self, slug: str, requester: Optional[str]) -> CustomDomainState:
        entity = self.repository.get_card_by_vanity(slug) or self.repository.get_card_by_uid(slug)
        if not entity:
            raise CustomDomainError("Cartao nao encontrado", "not_found", 404)
        owner = (entity.owner_email or "").strip()
        if not requester or requester != owner:
            raise CustomDomainError("Acesso negado.", "unauthorized", 403)
        card_meta = {"custom_domain": entity.custom_domain_meta or {}}
        meta = ensure_custom_domain_meta(card_meta)
        status = (meta.get("status") or "").lower()
        if status != CUSTOM_DOMAIN_STATUS_PENDING or not meta.get("requested_host"):
            raise CustomDomainError("Nenhuma solicitacao pendente.", "not_pending")
        meta.pop("requested_host", None)
        meta["status"] = CUSTOM_DOMAIN_STATUS_ACTIVE if active_custom_domain_host(card_meta) else CUSTOM_DOMAIN_STATUS_DISABLED
        meta["updated_at"] = int(time.time())
        self.repository.update_card_custom_domain_meta(entity.uid, meta)
        return CustomDomainState(meta.get("status", ""), meta.get("requested_host", ""), meta.get("active_host", ""))

    def remove(self, slug: str, requester: Optional[str]) -> CustomDomainState:
        entity = self.repository.get_card_by_vanity(slug) or self.repository.get_card_by_uid(slug)
        if not entity:
            raise CustomDomainError("Cartao nao encontrado", "not_found", 404)
        owner = (entity.owner_email or "").strip()
        if not requester or requester != owner:
            raise CustomDomainError("Acesso negado.", "unauthorized", 403)
        card_meta = {"custom_domain": entity.custom_domain_meta or {}}
        meta = ensure_custom_domain_meta(card_meta)
        active_host = meta.get("active_host")
        if not active_host:
            raise CustomDomainError("Nenhuma URL ativa para remover.", "no_active")
        unregister_custom_domain({}, active_host)
        meta["active_host"] = ""
        meta.pop("requested_host", None)
        meta["status"] = CUSTOM_DOMAIN_STATUS_DISABLED
        meta["updated_at"] = int(time.time())
        self.repository.update_card_custom_domain_meta(entity.uid, meta)
        return CustomDomainState(meta.get("status", ""), meta.get("requested_host", ""), meta.get("active_host", ""))
