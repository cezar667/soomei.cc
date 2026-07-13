from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from starlette.requests import Request

from api.core import config as core_config
from api.db import models
from api.db import session as db_session
from api.db.session import get_session
from api.routers import auth as _unused  # noqa: F401 - keeps package import parity in local runs
from api import admin_app


@pytest.fixture()
def admin_webhook_db(tmp_path, monkeypatch):
    db_file = tmp_path / "admin_webhooks.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    core_config.get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session._get_sessionmaker.cache_clear()  # type: ignore[attr-defined]

    engine = db_session.get_engine()
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)

    yield

    try:
        models.Base.metadata.drop_all(bind=engine)
    finally:
        engine.dispose()
        db_session.get_engine.cache_clear()
        db_session._get_sessionmaker.cache_clear()  # type: ignore[attr-defined]


def _request(path: str = "/webhooks") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8001")],
            "server": ("127.0.0.1", 8001),
            "client": ("127.0.0.1", 12345),
        }
    )


def _seed_webhook_data() -> str:
    now = datetime.now(timezone.utc)
    event_id = "evt-admin-internal"
    with get_session() as session:
        session.add(
            models.Member(
                id="member-admin",
                provider="themembers",
                external_customer_id="customer-admin",
                email="cliente@example.com",
                name="Cliente Admin",
            )
        )
        session.add(
            models.ExternalSubscription(
                id="sub-admin",
                provider="themembers",
                external_customer_id="customer-admin",
                external_subscription_id="order-admin",
                external_order_id="order-admin",
                external_product_id="soomei-card",
                member_id="member-admin",
                status="ACTIVE",
            )
        )
        session.add(
            models.Card(
                uid="uid-admin",
                pin="123456",
                status="active",
                vanity="cliente-admin",
                external_provider="themembers",
                external_subscription_id="order-admin",
                external_product_id="soomei-card",
                owner_email="cliente@example.com",
            )
        )
        session.add(
            models.WebhookEvent(
                id=event_id,
                provider="themembers",
                external_event_id="wh-admin-1",
                event_type="subscription.payment_approved",
                payload={
                    "event_id": "wh-admin-1",
                    "event_type": "subscription.payment_approved",
                    "data": {
                        "customer_id": "customer-admin",
                        "subscription_id": "order-admin",
                        "order_id": "order-admin",
                        "product_id": "soomei-card",
                    },
                },
                status="PROCESSED",
                attempts=1,
                received_at=now,
                processed_at=now,
            )
        )
        session.add(
            models.CardStatusHistory(
                id="hist-admin",
                card_uid="uid-admin",
                previous_status="pending",
                new_status="active",
                reason="PAYMENT_APPROVED",
                source="WEBHOOK",
                external_event_id="wh-admin-1",
                metadata_json={},
            )
        )
        session.commit()
    return event_id


def _allow_admin(monkeypatch):
    monkeypatch.setattr(admin_app, "require_admin", lambda _request: "admin@soomei.com.br")
    monkeypatch.setattr(admin_app, "_csrf_value", lambda _request: "csrf-admin")


def test_admin_webhooks_list_renders_events(admin_webhook_db, monkeypatch):
    _allow_admin(monkeypatch)
    _seed_webhook_data()

    response = admin_app.list_webhooks(_request("/webhooks"))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Eventos de webhook" in body
    assert "wh-admin-1" in body
    assert "subscription.payment_approved" in body
    assert "Processado" in body
    assert "/webhooks/subscriptions" in body


def test_admin_webhook_detail_renders_payload_and_related_card(admin_webhook_db, monkeypatch):
    _allow_admin(monkeypatch)
    event_id = _seed_webhook_data()

    response = admin_app.webhook_event_detail(event_id, _request(f"/webhooks/events/{event_id}"))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Payload recebido" in body
    assert "cliente@example.com" in body
    assert "uid-admin" in body
    assert "PAYMENT_APPROVED" in body
    assert "wh-admin-1" in body


def test_admin_external_subscriptions_renders_related_card(admin_webhook_db, monkeypatch):
    _allow_admin(monkeypatch)
    _seed_webhook_data()

    response = admin_app.list_external_subscriptions(_request("/webhooks/subscriptions"))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Assinaturas externas" in body
    assert "order-admin" in body
    assert "customer-admin" in body
    assert "uid-admin" in body


def test_admin_dashboard_renders_operational_charts(admin_webhook_db, monkeypatch):
    _allow_admin(monkeypatch)
    _seed_webhook_data()

    response = admin_app.dashboard(_request("/"), days=30)
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Dashboard operacional" in body
    assert "Evolução de cartões" in body
    assert "Volume de webhooks" in body
    assert "Criados no período" in body
    assert "Eventos no período" in body
    assert "Assinaturas externas" in body
    assert "Últimas falhas de webhook" in body
    assert "admin-line-chart" in body


def test_admin_can_grant_connector_badge_in_prod(admin_webhook_db, monkeypatch):
    _allow_admin(monkeypatch)
    monkeypatch.setattr(admin_app, "_csrf_protect", lambda _request, _token: None)
    monkeypatch.setattr(admin_app, "settings", type("Settings", (), {"app_env": "prod"})())
    with get_session() as session:
        session.add(
            models.Card(
                uid="uid-ref-dev",
                pin="123456",
                status="active",
                vanity="ref-dev",
                owner_email="cliente@example.com",
            )
        )
        session.commit()

    detail = admin_app.card_details("uid-ref-dev", _request("/cards/uid-ref-dev"))
    body = detail.body.decode("utf-8")
    assert "Ativar Destaque Soomei" in body
    assert "/cards/uid-ref-dev/connector-badge" in body

    response = admin_app.grant_connector_badge(
        "uid-ref-dev",
        _request("/cards/uid-ref-dev/connector-badge"),
        days=45,
        csrf_token="csrf-admin",
    )

    assert response.status_code == 303
    with get_session() as session:
        badge = session.execute(select(models.ProfileBadge).where(models.ProfileBadge.card_uid == "uid-ref-dev")).scalar_one()
    assert badge.badge_type == "soomei_connector"
    assert badge.label == "Destaque Soomei"
    assert badge.source == "admin_manual"
