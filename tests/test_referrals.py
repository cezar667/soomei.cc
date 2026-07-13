from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

from api.core.config import get_settings
from api.db import models
from api.db.session import _get_sessionmaker, get_engine, get_session
from api.referrals.enums import BadgeType, ReferralStatus, RewardType
from api.referrals.service import ReferralService
from api.repositories.sql_repository import SQLRepository


@pytest.fixture()
def referral_db(tmp_path, monkeypatch):
    db_file = tmp_path / "referrals.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()
    get_engine.cache_clear()
    _get_sessionmaker.cache_clear()
    engine = get_engine()
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        models.Base.metadata.drop_all(bind=engine)
        get_settings.cache_clear()
        get_engine.cache_clear()
        _get_sessionmaker.cache_clear()


def _seed_card(repo: SQLRepository, uid: str, email: str, vanity: str = ""):
    repo.upsert_user(email, password_hash="hash")
    repo.create_card(uid, "123456", vanity=vanity or None)
    repo.assign_card_owner(uid, email, status="active", vanity=vanity or None)


def test_referral_code_is_generated_for_card(referral_db):
    repo = SQLRepository()
    _seed_card(repo, "uid-a", "cezar@example.com", "cezar")
    service = ReferralService()

    code = service.ensure_code_for_card(card_uid="uid-a", owner_email="cezar@example.com", preferred="Cezar Damasceno")

    assert code.code
    assert code.owner_card_uid == "uid-a"
    assert service.ensure_code_for_card(card_uid="uid-a", owner_email="cezar@example.com").code == code.code


def test_apply_referral_waits_for_validation_before_granting_rewards(referral_db):
    repo = SQLRepository()
    _seed_card(repo, "uid-a", "a@example.com", "cliente-a")
    _seed_card(repo, "uid-b", "b@example.com", "cliente-b")
    service = ReferralService()
    code = service.ensure_code_for_card(card_uid="uid-a", owner_email="a@example.com", preferred="cliente-a")

    result = service.apply_onboarding_code(
        code=code.code,
        referred_card_uid="uid-b",
        referred_email="b@example.com",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert result.applied is True
    with get_session() as session:
        referral = session.execute(select(models.Referral)).scalar_one()
        assert referral.status == ReferralStatus.PENDING_VALIDATION.value
        assert referral.qualify_after is not None
        assert session.execute(select(models.ReferralReward)).scalars().all() == []
        assert session.execute(select(models.RaffleEntry)).scalars().all() == []
        assert session.execute(select(models.ProfileBadge)).scalars().all() == []

    referrer_summary = service.referral_summary(card_uid="uid-a", owner_email="a@example.com")
    assert referrer_summary.pending_referrals == 1
    assert referrer_summary.qualified_referrals == 0
    assert referrer_summary.badge_days_remaining == 0

    due_at = referral.qualify_after
    due_at = due_at if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
    stats = service.process_due_qualifications(now=due_at + timedelta(seconds=1))
    assert stats == {"processed": 1, "qualified": 1, "disqualified": 0}

    with get_session() as session:
        referral = session.execute(select(models.Referral)).scalar_one()
        assert referral.status == ReferralStatus.QUALIFIED.value
        rewards = session.execute(select(models.ReferralReward)).scalars().all()
        entries = session.execute(select(models.RaffleEntry)).scalars().all()
        badges = session.execute(select(models.ProfileBadge)).scalars().all()
        campaign = session.execute(select(models.ReferralCampaign)).scalar_one()
    assert {badge.card_uid for badge in badges} == {"uid-a"}
    assert all(badge.badge_type == BadgeType.SOOMEI_CONNECTOR.value for badge in badges)
    assert campaign.rules_json["beneficiary"] == "both"
    assert all(reward.reward_type == RewardType.RAFFLE_COUPON.value for reward in rewards)
    assert {reward.beneficiary_card_uid for reward in rewards} == {"uid-a", "uid-b"}
    assert len(entries) == 2
    assert {entry.card_uid for entry in entries} == {"uid-a", "uid-b"}
    assert {entry.entry_code.split("-")[2] for entry in entries} == {"REF", "IND"}
    referrer_summary = service.referral_summary(card_uid="uid-a", owner_email="a@example.com")
    referred_summary = service.referral_summary(card_uid="uid-b", owner_email="b@example.com")
    assert "👋 Olá!" in referrer_summary.share_message
    assert f"🎁 {code.code}" in referrer_summary.share_message
    assert "https://soomei.com.br" in referrer_summary.share_message
    assert referrer_summary.pending_referrals == 0
    assert referrer_summary.qualified_referrals == 1
    assert referrer_summary.badge_days_remaining > 0
    assert referrer_summary.raffle_coupons == 1
    assert referred_summary.badge_days_remaining == 0
    assert referred_summary.raffle_coupons == 1


def test_referral_rejects_self_referral(referral_db):
    repo = SQLRepository()
    _seed_card(repo, "uid-a", "a@example.com", "cliente-a")
    service = ReferralService()
    code = service.ensure_code_for_card(card_uid="uid-a", owner_email="a@example.com")

    result = service.apply_onboarding_code(
        code=code.code,
        referred_card_uid="uid-a",
        referred_email="a@example.com",
        ip_address=None,
        user_agent=None,
    )

    assert result.applied is False
    with get_session() as session:
        assert session.execute(select(models.Referral)).scalar_one_or_none() is None


def test_referral_badge_days_accumulate(referral_db):
    repo = SQLRepository()
    _seed_card(repo, "uid-a", "a@example.com", "cliente-a")
    _seed_card(repo, "uid-b", "b@example.com", "cliente-b")
    _seed_card(repo, "uid-c", "c@example.com", "cliente-c")
    service = ReferralService()
    code = service.ensure_code_for_card(card_uid="uid-a", owner_email="a@example.com")

    assert service.apply_onboarding_code(code=code.code, referred_card_uid="uid-b", referred_email="b@example.com", ip_address=None, user_agent=None).applied
    with get_session() as session:
        first_referral = session.execute(select(models.Referral).where(models.Referral.referred_card_uid == "uid-b")).scalar_one()
        first_due = first_referral.qualify_after
    first_due = first_due if first_due.tzinfo else first_due.replace(tzinfo=timezone.utc)
    service.process_due_qualifications(now=first_due + timedelta(seconds=1))
    first = service.referral_summary(card_uid="uid-a", owner_email="a@example.com").badge_expires_at
    assert first is not None
    assert service.apply_onboarding_code(code=code.code, referred_card_uid="uid-c", referred_email="c@example.com", ip_address=None, user_agent=None).applied
    with get_session() as session:
        second_referral = session.execute(select(models.Referral).where(models.Referral.referred_card_uid == "uid-c")).scalar_one()
        second_due = second_referral.qualify_after
    second_due = second_due if second_due.tzinfo else second_due.replace(tzinfo=timezone.utc)
    service.process_due_qualifications(now=second_due + timedelta(seconds=1))
    second = service.referral_summary(card_uid="uid-a", owner_email="a@example.com").badge_expires_at

    assert second is not None
    first_aware = first if first.tzinfo else first.replace(tzinfo=timezone.utc)
    second_aware = second if second.tzinfo else second.replace(tzinfo=timezone.utc)
    assert (second_aware - first_aware).days >= 29


def test_pending_referral_can_be_disqualified_by_access_loss_event(referral_db):
    repo = SQLRepository()
    _seed_card(repo, "uid-a", "a@example.com", "cliente-a")
    _seed_card(repo, "uid-b", "b@example.com", "cliente-b")
    service = ReferralService()
    code = service.ensure_code_for_card(card_uid="uid-a", owner_email="a@example.com")

    assert service.apply_onboarding_code(code=code.code, referred_card_uid="uid-b", referred_email="b@example.com", ip_address=None, user_agent=None).applied
    count = service.repository.disqualify_pending_for_referred_card(
        referred_card_uid="uid-b",
        reason="SUBSCRIPTION_CANCELLED",
    )

    assert count == 1
    with get_session() as session:
        referral = session.execute(select(models.Referral)).scalar_one()
        assert referral.status == ReferralStatus.DISQUALIFIED.value
        assert referral.rejection_reason == "SUBSCRIPTION_CANCELLED"
        assert session.execute(select(models.ProfileBadge)).scalar_one_or_none() is None
        assert session.execute(select(models.RaffleEntry)).scalar_one_or_none() is None


def test_referral_job_run_audit_records_success_and_failure(referral_db):
    service = ReferralService()

    success_run = service.repository.start_job_run(trigger="manual")
    service.repository.finish_job_run(
        success_run.id,
        result={"processed": 3, "qualified": 2, "disqualified": 1},
    )
    failed_run = service.repository.start_job_run(trigger="systemd")
    service.repository.fail_job_run(failed_run.id, error_message="database unavailable")

    runs = service.repository.recent_job_runs(limit=2)

    assert [run.status for run in runs] == ["failed", "success"]
    assert runs[0].error_message == "database unavailable"
    assert runs[1].processed_count == 3
    assert runs[1].qualified_count == 2
    assert runs[1].disqualified_count == 1
