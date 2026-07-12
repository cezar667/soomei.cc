from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from api.core.config import get_settings
from api.db import models
from api.db.session import _get_sessionmaker, get_engine, get_session
from scripts.prune_webhook_events import prune_webhook_payloads


@pytest.fixture()
def prune_db(tmp_path, monkeypatch):
    db_file = tmp_path / "prune_webhooks.db"
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


def _seed_event(event_id: str, *, status: str, received_at: datetime, payload: dict | None = None) -> None:
    payload = payload or {
        "event_id": event_id,
        "event_type": "subscription.payment_approved",
        "data": {
            "customer_id": "customer_123",
            "subscription_id": "subscription_456",
            "order_id": "order_789",
            "product_id": "soomei-card-black",
            "customer": {
                "name": "João da Silva",
                "email": "joao@example.com",
                "phone": "5534999999999",
                "document": "00000000000",
            },
        },
    }
    with get_session() as session:
        session.add(
            models.WebhookEvent(
                id=event_id,
                provider="themembers",
                external_event_id=event_id,
                event_type="subscription.payment_approved",
                payload=payload,
                status=status,
                received_at=received_at,
            )
        )
        session.commit()


def test_prune_webhook_payloads_dry_run_does_not_change_payload(prune_db):
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed_event("evt_old", status="PROCESSED", received_at=now - timedelta(days=100))

    result = prune_webhook_payloads(success_payload_days=90, dry_run=True, now=now)

    assert result.matched == 1
    assert result.pruned == 1
    with get_session() as session:
        event = session.execute(select(models.WebhookEvent)).scalar_one()
    assert event.payload["data"]["customer"]["email"] == "joao@example.com"


def test_prune_webhook_payloads_compacts_personal_payload(prune_db):
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed_event("evt_old", status="PROCESSED", received_at=now - timedelta(days=100))

    result = prune_webhook_payloads(success_payload_days=90, dry_run=False, now=now)

    assert result.matched == 1
    assert result.pruned == 1
    with get_session() as session:
        event = session.execute(select(models.WebhookEvent)).scalar_one()
    assert event.payload["_retention"]["pruned"] is True
    assert event.payload["event_id"] == "evt_old"
    assert event.payload["data"]["customer_id"] == "customer_123"
    assert "customer" not in event.payload["data"]


def test_prune_webhook_payloads_keeps_recent_and_retry_pending_events(prune_db):
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed_event("evt_recent", status="PROCESSED", received_at=now - timedelta(days=10))
    _seed_event("evt_retry", status="RETRY_PENDING", received_at=now - timedelta(days=300))

    result = prune_webhook_payloads(success_payload_days=90, error_payload_days=180, dry_run=False, now=now)

    assert result.matched == 0
    assert result.pruned == 0
    with get_session() as session:
        events = session.execute(select(models.WebhookEvent).order_by(models.WebhookEvent.id)).scalars().all()
    assert all("_retention" not in event.payload for event in events)
