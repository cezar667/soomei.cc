from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from api.db import models
from api.db.session import get_session
from api.referrals.enums import BadgeType, CampaignStatus, ReferralCodeStatus, ReferralStatus, RewardType


class ReferralRepository:
    def get_code(self, code: str) -> models.ReferralCode | None:
        with get_session() as session:
            stmt = select(models.ReferralCode).where(func.upper(models.ReferralCode.code) == code.upper()).limit(1)
            return session.execute(stmt).scalar_one_or_none()

    def get_code_by_owner_card(self, card_uid: str) -> models.ReferralCode | None:
        with get_session() as session:
            stmt = (
                select(models.ReferralCode)
                .where(models.ReferralCode.owner_card_uid == card_uid)
                .order_by(models.ReferralCode.created_at.asc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()

    def code_exists(self, code: str) -> bool:
        return self.get_code(code) is not None

    def create_referral_code(self, *, code_id: str, code: str, owner_card_uid: str, owner_email: str | None) -> models.ReferralCode:
        now = datetime.now(timezone.utc)
        entity = models.ReferralCode(
            id=code_id,
            code=code,
            owner_card_uid=owner_card_uid,
            owner_email=owner_email,
            status=ReferralCodeStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        )
        with get_session() as session:
            session.add(entity)
            session.commit()
            session.refresh(entity)
            return entity

    def count_qualified_referrals(self, owner_card_uid: str) -> int:
        with get_session() as session:
            stmt = select(func.count(models.Referral.id)).where(
                models.Referral.referrer_card_uid == owner_card_uid,
                models.Referral.status == ReferralStatus.QUALIFIED.value,
            )
            return int(session.execute(stmt).scalar() or 0)

    def pending_validation_summary(self, owner_card_uid: str) -> tuple[int, datetime | None]:
        with get_session() as session:
            stmt = select(
                func.count(models.Referral.id),
                func.min(models.Referral.qualify_after),
            ).where(
                models.Referral.referrer_card_uid == owner_card_uid,
                models.Referral.status == ReferralStatus.PENDING_VALIDATION.value,
            )
            count, next_at = session.execute(stmt).one()
            return int(count or 0), next_at

    def count_raffle_coupons(self, card_uid: str) -> int:
        with get_session() as session:
            stmt = select(func.count(models.RaffleEntry.id)).where(
                models.RaffleEntry.card_uid == card_uid,
                models.RaffleEntry.status == "active",
            )
            return int(session.execute(stmt).scalar() or 0)

    def active_badge(self, card_uid: str, *, now: datetime | None = None) -> models.ProfileBadge | None:
        current = now or datetime.now(timezone.utc)
        try:
            with get_session() as session:
                stmt = (
                    select(models.ProfileBadge)
                    .where(
                        models.ProfileBadge.card_uid == card_uid,
                        models.ProfileBadge.badge_type == BadgeType.SOOMEI_CONNECTOR.value,
                        models.ProfileBadge.expires_at > current,
                    )
                    .limit(1)
                )
                return session.execute(stmt).scalar_one_or_none()
        except SQLAlchemyError:
            return None

    def ensure_default_campaign(self) -> models.ReferralCampaign:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            stmt = select(models.ReferralCampaign).where(models.ReferralCampaign.slug == "pix-da-virada-2026").limit(1)
            campaign = session.execute(stmt).scalar_one_or_none()
            if campaign:
                return campaign
            campaign = models.ReferralCampaign(
                id=_new_id(),
                slug="pix-da-virada-2026",
                name="Pix da Virada",
                description="Cupom promocional anual gerado por indicações qualificadas.",
                status=CampaignStatus.ACTIVE.value,
                rules_json={"coupon_per_qualified_referral": 1, "beneficiary": "both"},
                created_at=now,
                updated_at=now,
            )
            session.add(campaign)
            session.commit()
            session.refresh(campaign)
            return campaign

    def apply_referral(
        self,
        *,
        referral_id: str,
        referral_code: models.ReferralCode,
        referred_card_uid: str,
        referred_email: str,
        ip_address: str | None,
        user_agent: str | None,
        now: datetime,
        badge_days: int,
        qualification_days: int,
        campaign_enabled: bool = True,
    ) -> tuple[bool, str]:
        """Create a pending referral atomically. Returns (applied, message)."""
        with get_session() as session:
            existing = session.execute(
                select(models.Referral).where(models.Referral.referred_card_uid == referred_card_uid).limit(1)
            ).scalar_one_or_none()
            if existing:
                return False, "Este cartão já possui uma indicação registrada."

            code = session.get(models.ReferralCode, referral_code.id)
            if not code or code.status != ReferralCodeStatus.ACTIVE.value:
                return False, "Código de indicação não encontrado ou inativo."
            if code.owner_card_uid == referred_card_uid:
                return False, "Não é possível usar o próprio código de indicação."
            if (code.owner_email or "").strip().lower() == (referred_email or "").strip().lower():
                return False, "Não é possível usar o próprio código de indicação."

            referral = models.Referral(
                id=referral_id,
                referral_code_id=code.id,
                code_used=code.code,
                referrer_card_uid=code.owner_card_uid,
                referrer_email=code.owner_email,
                referred_card_uid=referred_card_uid,
                referred_email=referred_email,
                status=ReferralStatus.PENDING_VALIDATION.value,
                qualify_after=now + timedelta(days=max(0, qualification_days)),
                source="onboarding",
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=now,
                updated_at=now,
            )
            session.add(referral)

            session.commit()
            return True, "Código aplicado: indicação registrada e benefícios em validação por 30 dias."

    def process_due_qualifications(
        self,
        *,
        now: datetime | None = None,
        limit: int = 500,
        badge_days: int = 30,
        campaign_enabled: bool = True,
    ) -> dict[str, int]:
        """Qualify or disqualify referrals whose validation window has ended."""
        current = now or datetime.now(timezone.utc)
        current = _ensure_aware(current)
        safe_limit = max(1, int(limit or 500))
        stats = {"processed": 0, "qualified": 0, "disqualified": 0}
        with get_session() as session:
            due_referrals = (
                session.execute(
                    select(models.Referral)
                    .where(
                        models.Referral.status == ReferralStatus.PENDING_VALIDATION.value,
                        models.Referral.qualify_after.is_not(None),
                        models.Referral.qualify_after <= current,
                    )
                    .order_by(models.Referral.qualify_after.asc(), models.Referral.created_at.asc())
                    .limit(safe_limit)
                )
                .scalars()
                .all()
            )
            if not due_referrals:
                return stats

            campaign = None
            if campaign_enabled:
                campaign = session.execute(
                    select(models.ReferralCampaign)
                    .where(
                        models.ReferralCampaign.slug == "pix-da-virada-2026",
                        models.ReferralCampaign.status == CampaignStatus.ACTIVE.value,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if not campaign:
                    campaign = models.ReferralCampaign(
                        id=_new_id(),
                        slug="pix-da-virada-2026",
                        name="Pix da Virada",
                        description="Cupom promocional anual gerado por indicações qualificadas.",
                        status=CampaignStatus.ACTIVE.value,
                        rules_json={"coupon_per_qualified_referral": 1, "beneficiary": "both"},
                        created_at=current,
                        updated_at=current,
                    )
                    session.add(campaign)
                    session.flush()

            for referral in due_referrals:
                stats["processed"] += 1
                referral.status = ReferralStatus.QUALIFIED.value
                referral.qualified_at = current
                referral.rejection_reason = None
                referral.updated_at = current
                self._grant_badge_locked(
                    session,
                    card_uid=referral.referrer_card_uid,
                    email=referral.referrer_email,
                    referral=referral,
                    days=badge_days,
                    now=current,
                )
                if campaign:
                    self._grant_raffle_coupon_locked(
                        session,
                        campaign=campaign,
                        referral=referral,
                        card_uid=referral.referrer_card_uid,
                        email=referral.referrer_email,
                        role="referrer",
                        now=current,
                    )
                    self._grant_raffle_coupon_locked(
                        session,
                        campaign=campaign,
                        referral=referral,
                        card_uid=referral.referred_card_uid,
                        email=referral.referred_email,
                        role="referred",
                        now=current,
                    )
                stats["qualified"] += 1
            session.commit()
        return stats

    def disqualify_pending_for_referred_card(
        self,
        *,
        referred_card_uid: str,
        reason: str,
        now: datetime | None = None,
    ) -> int:
        current = now or datetime.now(timezone.utc)
        with get_session() as session:
            referrals = (
                session.execute(
                    select(models.Referral).where(
                        models.Referral.referred_card_uid == referred_card_uid,
                        models.Referral.status == ReferralStatus.PENDING_VALIDATION.value,
                    )
                )
                .scalars()
                .all()
            )
            for referral in referrals:
                referral.status = ReferralStatus.DISQUALIFIED.value
                referral.rejected_at = current
                referral.rejection_reason = reason
                referral.updated_at = current
            session.commit()
            return len(referrals)

    def _grant_badge_locked(self, session, *, card_uid: str, email: str | None, referral: models.Referral, days: int, now: datetime) -> None:
        badge = session.execute(
            select(models.ProfileBadge)
            .where(
                models.ProfileBadge.card_uid == card_uid,
                models.ProfileBadge.badge_type == BadgeType.SOOMEI_CONNECTOR.value,
            )
            .limit(1)
        ).scalar_one_or_none()
        current_expiry = _ensure_aware(badge.expires_at) if badge and badge.expires_at else None
        base = max(now, current_expiry) if current_expiry else now
        expires_at = base + timedelta(days=days)
        if badge:
            badge.label = "Destaque Soomei"
            badge.expires_at = expires_at
            badge.source = "referral"
            badge.source_id = referral.id
            badge.updated_at = now
        else:
            session.add(
                models.ProfileBadge(
                    id=_new_id(),
                    card_uid=card_uid,
                    badge_type=BadgeType.SOOMEI_CONNECTOR.value,
                    label="Destaque Soomei",
                    starts_at=now,
                    expires_at=expires_at,
                    source="referral",
                    source_id=referral.id,
                    created_at=now,
                    updated_at=now,
                )
            )

    def _grant_raffle_coupon_locked(
        self,
        session,
        *,
        campaign: models.ReferralCampaign,
        referral: models.Referral,
        card_uid: str,
        email: str | None,
        role: str,
        now: datetime,
    ) -> None:
        reward = models.ReferralReward(
            id=_new_id(),
            referral_id=referral.id,
            campaign_id=campaign.id,
            beneficiary_card_uid=card_uid,
            beneficiary_email=email,
            reward_type=RewardType.RAFFLE_COUPON.value,
            quantity=1,
            metadata_json={"campaign_slug": campaign.slug, "beneficiary_role": role},
            status="granted",
            granted_at=now,
            created_at=now,
        )
        session.add(reward)
        session.flush()
        role_prefix = "IND" if role == "referred" else "REF"
        entry_code = f"PDV-{now.year}-{role_prefix}-{card_uid[:6].upper()}-{referral.id[:8].upper()}"
        session.add(
            models.RaffleEntry(
                id=_new_id(),
                campaign_id=campaign.id,
                reward_id=reward.id,
                card_uid=card_uid,
                email=email,
                entry_code=entry_code,
                status="active",
                created_at=now,
            )
        )


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
