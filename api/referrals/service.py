from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime, timezone

from api.core.config import get_settings
from api.referrals.repository import ReferralRepository
from api.referrals.schemas import ReferralApplicationResult, ReferralSummary
from sqlalchemy.exc import SQLAlchemyError


BADGE_DAYS_PER_REFERRAL = 30
RESERVED_CODES = {
    "SOOMEI",
    "ADMIN",
    "SUPORTE",
    "OFICIAL",
    "VERIFICADO",
    "NULL",
    "NONE",
}


class ReferralService:
    def __init__(self, repository: ReferralRepository | None = None):
        self.repository = repository or ReferralRepository()
        self.settings = get_settings()

    def normalize_code(self, value: str) -> str:
        raw = unicodedata.normalize("NFKD", value or "")
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = raw.upper().strip()
        raw = re.sub(r"[^A-Z0-9-]+", "", raw)
        raw = re.sub(r"-{2,}", "-", raw).strip("-")
        return raw[:40]

    def ensure_code_for_card(self, *, card_uid: str, owner_email: str | None = None, preferred: str | None = None):
        existing = self.repository.get_code_by_owner_card(card_uid)
        if existing:
            return existing
        preferred_base = preferred or ((owner_email or "").split("@", 1)[0] if owner_email and "@" in owner_email else card_uid)
        base = self.normalize_code(preferred_base)
        base = (base or "SOOMEI").replace("-", "")
        if len(base) < 4 or base in RESERVED_CODES:
            base = "SOOMEI"
        for _ in range(30):
            suffix = secrets.token_hex(2).upper()
            code = self.normalize_code(f"{base[:10]}{suffix}")
            if code in RESERVED_CODES:
                continue
            if not self.repository.code_exists(code):
                return self.repository.create_referral_code(
                    code_id=_new_id(),
                    code=code,
                    owner_card_uid=card_uid,
                    owner_email=owner_email,
                )
        raise RuntimeError("Nao foi possivel gerar codigo de indicacao unico.")

    def referral_summary(self, *, card_uid: str, owner_email: str | None, preferred: str | None = None) -> ReferralSummary:
        try:
            code = self.ensure_code_for_card(card_uid=card_uid, owner_email=owner_email, preferred=preferred)
        except SQLAlchemyError:
            return ReferralSummary(
                code="INDISPONIVEL",
                badge_expires_at=None,
                badge_days_remaining=0,
                qualified_referrals=0,
                pending_referrals=0,
                next_qualification_at=None,
                raffle_coupons=0,
                share_message="O código de indicação ficará disponível após atualização do banco de dados.",
            )
        now = datetime.now(timezone.utc)
        badge = self.repository.active_badge(card_uid, now=now)
        days_remaining = 0
        badge_expires_at = None
        if badge and badge.expires_at:
            badge_expires_at = badge.expires_at if badge.expires_at.tzinfo else badge.expires_at.replace(tzinfo=timezone.utc)
            days_remaining = max(0, int((badge_expires_at - now).total_seconds() // 86400) + 1)
        pending_count, next_qualification_at = self.repository.pending_validation_summary(card_uid)
        share_url = "https://soomei.com.br"
        share_message = (
            "👋 Olá!\n\n"
            "Quero te fazer um convite especial.\n\n"
            "Faço parte da Soomei, uma associação criada para conectar e impulsionar empreendedores "
            "e microempreendedores de todo o Brasil, e tem sido uma experiência muito positiva para mim.\n\n"
            "Quero fazer uma indicação especial para você! Ao se cadastrar na Soomei, utilize o meu código de indicação para aproveitar ainda mais vantagens e benefícios exclusivos:\n\n"
            f"🎁 {code.code}\n\n"
            "Com ele, você poderá ativar benefícios exclusivos disponíveis para novos associados.\n\n"
            "Tenho certeza de que você vai gostar de fazer parte dessa comunidade. Vai ser muito bom ter você com a gente!\n\n"
            f"🚀 Acesse agora: {share_url}"
        )
        return ReferralSummary(
            code=code.code,
            badge_expires_at=badge_expires_at,
            badge_days_remaining=days_remaining,
            qualified_referrals=self.repository.count_qualified_referrals(card_uid),
            pending_referrals=pending_count,
            next_qualification_at=next_qualification_at,
            raffle_coupons=self.repository.count_raffle_coupons(card_uid),
            share_message=share_message,
        )

    def apply_onboarding_code(
        self,
        *,
        code: str,
        referred_card_uid: str,
        referred_email: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ReferralApplicationResult:
        normalized = self.normalize_code(code)
        if not normalized:
            return ReferralApplicationResult(applied=False, message="")
        referral_code = self.repository.get_code(normalized)
        if not referral_code:
            return ReferralApplicationResult(
                applied=False,
                message="Código de indicação não encontrado. Você pode continuar sem indicação.",
            )
        applied, message = self.repository.apply_referral(
            referral_id=_new_id(),
            referral_code=referral_code,
            referred_card_uid=referred_card_uid,
            referred_email=referred_email,
            ip_address=ip_address,
            user_agent=user_agent,
            now=datetime.now(timezone.utc),
            badge_days=BADGE_DAYS_PER_REFERRAL,
            qualification_days=self.settings.referral_qualification_days,
        )
        if applied:
            days = self.settings.referral_qualification_days
            message = (
                f"Código aplicado: indicação registrada. Os benefícios serão liberados após {days} dia(s), "
                "se a assinatura do indicado permanecer ativa."
            )
        return ReferralApplicationResult(applied=applied, message=message)

    def process_due_qualifications(self, *, limit: int | None = None, now: datetime | None = None) -> dict[str, int]:
        return self.repository.process_due_qualifications(
            now=now,
            limit=limit or self.settings.referral_qualification_batch_size,
            badge_days=BADGE_DAYS_PER_REFERRAL,
        )


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
