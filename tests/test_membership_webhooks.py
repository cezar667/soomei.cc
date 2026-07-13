from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.core.config import get_settings
from api.db import models
from api.db.session import _get_sessionmaker, get_engine, get_session
from api.integrations.membership_platform import router as membership_router
from api.integrations.membership_platform.enums import CardStatusReason, WebhookEventStatus
from api.integrations.membership_platform.exceptions import WebhookAuthenticationError
from api.integrations.membership_platform.signature import build_test_signature, validate_webhook_signature
from api.referrals.enums import ReferralStatus


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    db_file = tmp_path / "membership_webhooks.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_SECRET", "current-secret")
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_PREVIOUS_SECRET", "previous-secret")
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_PROVIDER", "membership_platform")
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


@pytest.fixture()
def direct_endpoint(db_env):
    return True


class FakeRequest:
    def __init__(self, raw_body: bytes, headers: dict[str, str]):
        self._raw_body = raw_body
        self.headers = headers
        self.url = SimpleNamespace(scheme="http")
        self.client = SimpleNamespace(host="127.0.0.1")

    async def body(self) -> bytes:
        return self._raw_body


def _payload(event_id: str = "evt_1", event_type: str = "subscription.payment_approved") -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "customer_id": "customer_123",
            "subscription_id": "subscription_456",
            "order_id": "order_789",
            "product_id": "soomei-card-black",
            "plan_id": "plan_monthly",
            "customer": {
                "name": "João da Silva",
                "email": "joao@example.com",
                "phone": "5534999999999",
                "document": "00000000000",
            },
        },
    }


def _signed_headers(raw_body: bytes, *, event_id: str = "evt_1", secret: str = "current-secret") -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature": build_test_signature(secret=secret, timestamp=timestamp, raw_body=raw_body),
        "X-Webhook-Event-Id": event_id,
        "X-Correlation-Id": "corr-test",
    }


async def _post_event_async(payload: dict, *, secret: str = "current-secret", signature_override: str | None = None):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = _signed_headers(raw, event_id=payload["event_id"], secret=secret)
    if signature_override is not None:
        headers["X-Webhook-Signature"] = signature_override
    request = FakeRequest(raw, headers)
    return await membership_router.receive_membership_webhook(
        request,
        x_webhook_timestamp=headers["X-Webhook-Timestamp"],
        x_webhook_signature=headers["X-Webhook-Signature"],
        x_webhook_event_id=headers["X-Webhook-Event-Id"],
        x_correlation_id=headers["X-Correlation-Id"],
    )


def _post_event(payload: dict, *, secret: str = "current-secret", signature_override: str | None = None):
    import asyncio

    return asyncio.run(
        _post_event_async(payload, secret=secret, signature_override=signature_override)
    )


async def _post_themembers_event_async(payload: dict):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {"X-Correlation-Id": "corr-themembers"}
    request = FakeRequest(raw, headers)
    return await membership_router.receive_membership_webhook(
        request,
        x_webhook_timestamp=None,
        x_webhook_signature=None,
        x_webhook_event_id=None,
        x_correlation_id=headers["X-Correlation-Id"],
    )


def _post_themembers_event(payload: dict):
    import asyncio

    return asyncio.run(_post_themembers_event_async(payload))


def _themembers_payload(
    event_id: str = "wh_1",
    event: str = "transaction.approved",
    *,
    token: str = "current-secret",
) -> dict:
    return {
        "id": event_id,
        "object": "transaction",
        "event": event,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "token": token,
        "data": {
            "id": "txn_123",
            "paid_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "payment_details": {
                "payment_method": "pix",
                "pix": {
                    "qrcode_url": "https://example.com/qr",
                    "qrcode_data": "000201010212",
                    "expiration": "2026-07-13 05:05:05",
                },
            },
            "transaction": {
                "currency": "brl",
                "amount": 40.0,
                "buyer_fees": 2.33,
                "total_amount": 42.33,
            },
            "customer": {
                "id": "customer_themembers_123",
                "name": "Cliente TheMembers",
                "email": "cliente.themembers@example.com",
                "phone": "5534999999999",
                "document": "00000000000",
            },
            "product": {
                "id": "soomei-card-black",
                "name": "Soomei Card Black",
            },
            "order": {
                "id": "order_themembers_123",
            },
        },
    }


def test_signature_valid_invalid_expired_and_previous_secret():
    raw = b'{"event_id":"evt"}'
    timestamp = str(int(time.time()))
    valid = build_test_signature(secret="current", timestamp=timestamp, raw_body=raw)
    validate_webhook_signature(
        secrets=["current"],
        timestamp=timestamp,
        received_signature=valid,
        raw_body=raw,
        max_delay_seconds=300,
    )

    previous = build_test_signature(secret="previous", timestamp=timestamp, raw_body=raw)
    validate_webhook_signature(
        secrets=["current", "previous"],
        timestamp=timestamp,
        received_signature=previous,
        raw_body=raw,
        max_delay_seconds=300,
    )

    with pytest.raises(WebhookAuthenticationError):
        validate_webhook_signature(
            secrets=["current"],
            timestamp=timestamp,
            received_signature=valid,
            raw_body=b'{"event_id":"altered"}',
            max_delay_seconds=300,
        )

    with pytest.raises(WebhookAuthenticationError):
        validate_webhook_signature(
            secrets=["current"],
            timestamp=str(int(time.time()) - 9999),
            received_signature=valid,
            raw_body=raw,
            max_delay_seconds=300,
        )


def test_payment_approved_is_idempotent_and_creates_pending_card(direct_endpoint):
    payload = _payload("evt_payment_1")
    first = _post_event(payload)
    second = _post_event(payload)

    assert first["received"] is True
    assert first["duplicate"] is False
    assert second["duplicate"] is True

    with get_session() as session:
        events = session.execute(select(models.WebhookEvent)).scalars().all()
        cards = session.execute(select(models.Card)).scalars().all()
        subscription = session.execute(select(models.ExternalSubscription)).scalar_one()
        member = session.execute(select(models.Member)).scalar_one()

    assert len(events) == 1
    assert events[0].status == WebhookEventStatus.PROCESSED.value
    assert len(cards) == 1
    assert cards[0].status == "pending"
    assert cards[0].owner_email == "joao@example.com"
    assert cards[0].external_subscription_id == "subscription_456"
    assert subscription.status == "ACTIVE"
    assert member.external_customer_id == "customer_123"


def test_overdue_blocks_and_reactivation_only_restores_payment_suspension(direct_endpoint):
    assert _post_event(_payload("evt_payment_ok"))["received"] is True
    assert _post_event(_payload("evt_overdue", "subscription.overdue"))["received"] is True

    with get_session() as session:
        card = session.execute(select(models.Card)).scalar_one()
        assert card.status == "blocked"
        assert card.status_reason == CardStatusReason.PAYMENT_OVERDUE.value

    assert _post_event(_payload("evt_reactivated", "subscription.reactivated"))["received"] is True
    with get_session() as session:
        card = session.execute(select(models.Card)).scalar_one()
        assert card.status == "active"
        assert card.status_reason == CardStatusReason.PAYMENT_REGULARIZED.value
        history = session.execute(select(models.CardStatusHistory).order_by(models.CardStatusHistory.created_at)).scalars().all()
        assert [item.reason for item in history] == [
            CardStatusReason.WEBHOOK_CREATED.value,
            CardStatusReason.PAYMENT_OVERDUE.value,
            CardStatusReason.PAYMENT_REGULARIZED.value,
        ]

    with get_session() as session:
        card = session.execute(select(models.Card)).scalar_one()
        card.status = "blocked"
        card.status_reason = "REPORTED_LOST"
        session.commit()

    assert _post_event(_payload("evt_reactivated_again", "subscription.reactivated"))["received"] is True
    with get_session() as session:
        card = session.execute(select(models.Card)).scalar_one()
        assert card.status == "blocked"
        assert card.status_reason == "REPORTED_LOST"


def test_unknown_event_is_persisted_and_ignored(direct_endpoint):
    response = _post_event(_payload("evt_unknown", "subscription.something_new"))

    assert response["received"] is True
    with get_session() as session:
        event = session.execute(select(models.WebhookEvent)).scalar_one()
    assert event.status == WebhookEventStatus.IGNORED.value
    assert event.error_code == "unsupported_event_type"


def test_endpoint_rejects_invalid_signature(direct_endpoint):
    with pytest.raises(HTTPException) as exc:
        _post_event(_payload("evt_bad_sig"), signature_override="sha256=bad")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid webhook authentication"


def test_themembers_payload_token_creates_card_and_is_idempotent(db_env, monkeypatch):
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_PROVIDER", "themembers")
    get_settings.cache_clear()

    payload = _themembers_payload("wh_themembers_approved")
    first = _post_themembers_event(payload)
    second = _post_themembers_event(payload)

    assert first["received"] is True
    assert first["event_id"] == "wh_themembers_approved"
    assert first["duplicate"] is False
    assert second["duplicate"] is True

    with get_session() as session:
        event = session.execute(select(models.WebhookEvent)).scalar_one()
        card = session.execute(select(models.Card)).scalar_one()
        subscription = session.execute(select(models.ExternalSubscription)).scalar_one()
        member = session.execute(select(models.Member)).scalar_one()

    assert event.event_type == "subscription.payment_approved"
    assert event.payload["data"]["native_provider"] == "themembers"
    assert event.payload["data"]["native_event"] == "transaction.approved"
    assert card.status == "pending"
    assert card.owner_email == "cliente.themembers@example.com"
    assert card.external_subscription_id == "order_themembers_123"
    assert card.external_product_id == "soomei-card-black"
    assert subscription.status == "ACTIVE"
    assert member.external_customer_id == "customer_themembers_123"


def test_themembers_access_removed_blocks_existing_card(db_env, monkeypatch):
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_PROVIDER", "themembers")
    get_settings.cache_clear()

    assert _post_themembers_event(_themembers_payload("wh_access_granted", "access.granted"))["received"] is True
    with get_session() as session:
        referred_card = session.execute(select(models.Card)).scalar_one()
        session.add(models.User(email="indicador@example.com", password_hash="hash"))
        session.add(models.Card(uid="uid-indicador", pin="123456", status="active", owner_email="indicador@example.com"))
        session.add(
            models.Referral(
                id="referral-webhook-disqualify",
                referral_code_id=None,
                code_used="INDICADOR",
                referrer_card_uid="uid-indicador",
                referrer_email="indicador@example.com",
                referred_card_uid=referred_card.uid,
                referred_email=referred_card.owner_email,
                status=ReferralStatus.PENDING_VALIDATION.value,
                qualify_after=datetime.now(timezone.utc),
                source="onboarding",
            )
        )
        session.commit()

    assert _post_themembers_event(_themembers_payload("wh_access_removed", "access.removed"))["received"] is True

    with get_session() as session:
        card = session.execute(
            select(models.Card).where(models.Card.external_subscription_id == "order_themembers_123")
        ).scalar_one()
        referral = session.execute(select(models.Referral)).scalar_one()
        assert card.status == "blocked"
        assert card.status_reason == CardStatusReason.SUBSCRIPTION_CANCELLED.value
        assert referral.status == ReferralStatus.DISQUALIFIED.value
        assert referral.rejection_reason == CardStatusReason.SUBSCRIPTION_CANCELLED.value


def test_themembers_invalid_payload_token_is_rejected(db_env, monkeypatch):
    monkeypatch.setenv("MEMBERSHIP_WEBHOOK_PROVIDER", "themembers")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc:
        _post_themembers_event(_themembers_payload("wh_bad_token", token="wrong-token"))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid webhook authentication"
