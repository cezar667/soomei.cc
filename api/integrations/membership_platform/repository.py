"""Persistence helpers for membership platform webhooks."""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from api.core.security import hash_password
from api.db.models import Card, CardStatusHistory, ExternalSubscription, Member, User, WebhookEvent
from api.db.session import get_session

from .enums import CardStatusReason, CardStatusSource, SubscriptionStatus, WebhookEventStatus


@dataclass(frozen=True)
class RegisteredWebhookEvent:
    event: WebhookEvent
    duplicate: bool


class MembershipRepository:
    """Repository dedicated to the external membership integration."""

    def register_webhook_event(
        self,
        *,
        provider: str,
        external_event_id: str,
        event_type: str,
        payload: dict,
        correlation_id: str,
    ) -> RegisteredWebhookEvent:
        now = datetime.now(timezone.utc)
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            provider=provider,
            external_event_id=external_event_id,
            event_type=event_type,
            payload=payload,
            status=WebhookEventStatus.RECEIVED.value,
            attempts=0,
            correlation_id=correlation_id,
            received_at=now,
        )
        with get_session() as session:
            session.add(event)
            try:
                session.commit()
                session.refresh(event)
                return RegisteredWebhookEvent(event=event, duplicate=False)
            except IntegrityError:
                session.rollback()
                stmt = select(WebhookEvent).where(
                    WebhookEvent.provider == provider,
                    WebhookEvent.external_event_id == external_event_id,
                )
                existing = session.execute(stmt).scalar_one()
                return RegisteredWebhookEvent(event=existing, duplicate=True)

    def get_webhook_event(self, event_id: str) -> WebhookEvent | None:
        with get_session() as session:
            return session.get(WebhookEvent, event_id)

    def claim_event_for_processing(self, event_id: str) -> WebhookEvent | None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            event = session.get(WebhookEvent, event_id)
            if not event or event.status not in {
                WebhookEventStatus.RECEIVED.value,
                WebhookEventStatus.RETRY_PENDING.value,
                WebhookEventStatus.FAILED.value,
            }:
                return None
            event.status = WebhookEventStatus.PROCESSING.value
            event.attempts = int(event.attempts or 0) + 1
            event.processing_started_at = now
            event.error_code = None
            event.error_message = None
            session.commit()
            session.refresh(event)
            return event

    def mark_event_processed(self, event_id: str) -> None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            event = session.get(WebhookEvent, event_id)
            if event:
                event.status = WebhookEventStatus.PROCESSED.value
                event.processed_at = now
                event.next_retry_at = None
                event.error_code = None
                event.error_message = None
                session.commit()

    def mark_event_ignored(self, event_id: str, *, code: str, message: str) -> None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            event = session.get(WebhookEvent, event_id)
            if event:
                event.status = WebhookEventStatus.IGNORED.value
                event.processed_at = now
                event.error_code = code[:100]
                event.error_message = message[:2000]
                event.next_retry_at = None
                session.commit()

    def mark_event_failed(
        self,
        event_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
        max_retries: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            event = session.get(WebhookEvent, event_id)
            if not event:
                return
            attempts = int(event.attempts or 0)
            event.error_code = code[:100]
            event.error_message = message[:2000]
            if retryable and attempts < int(max_retries or 0):
                event.status = WebhookEventStatus.RETRY_PENDING.value
                event.next_retry_at = now + self._retry_delay(attempts)
            elif retryable:
                event.status = WebhookEventStatus.DEAD_LETTER.value
                event.next_retry_at = None
            else:
                event.status = WebhookEventStatus.FAILED.value
                event.next_retry_at = None
            session.commit()

    def pending_webhook_events(self, *, limit: int = 50) -> list[WebhookEvent]:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            stmt = (
                select(WebhookEvent)
                .where(
                    or_(
                        WebhookEvent.status == WebhookEventStatus.RECEIVED.value,
                        (
                            (WebhookEvent.status == WebhookEventStatus.RETRY_PENDING.value)
                            & (
                                (WebhookEvent.next_retry_at.is_(None))
                                | (WebhookEvent.next_retry_at <= now)
                            )
                        ),
                    )
                )
                .order_by(WebhookEvent.received_at.asc())
                .limit(max(1, int(limit or 50)))
            )
            return session.execute(stmt).scalars().all()

    @staticmethod
    def _retry_delay(attempts: int) -> timedelta:
        delays = [60, 300, 900, 3600]
        idx = max(0, min(max(0, attempts - 1), len(delays) - 1))
        return timedelta(seconds=delays[idx])

    def upsert_member_and_subscription(
        self,
        *,
        provider: str,
        external_customer_id: str,
        external_subscription_id: str | None,
        external_order_id: str | None,
        external_product_id: str | None,
        external_plan_id: str | None,
        customer: dict,
        subscription_status: SubscriptionStatus,
    ) -> tuple[Member, ExternalSubscription]:
        now = datetime.now(timezone.utc)
        email = (customer.get("email") or "").strip().lower() or None
        with get_session() as session:
            stmt = select(Member).where(
                Member.provider == provider,
                Member.external_customer_id == external_customer_id,
            )
            member = session.execute(stmt).scalar_one_or_none()
            if not member:
                member = Member(
                    id=str(uuid.uuid4()),
                    provider=provider,
                    external_customer_id=external_customer_id,
                    email=email,
                    name=(customer.get("name") or "").strip() or None,
                    phone=(customer.get("phone") or "").strip() or None,
                    document=(customer.get("document") or "").strip() or None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(member)
            else:
                member.email = email or member.email
                member.name = (customer.get("name") or "").strip() or member.name
                member.phone = (customer.get("phone") or "").strip() or member.phone
                member.document = (customer.get("document") or "").strip() or member.document
                member.updated_at = now

            if email and not session.get(User, email):
                session.add(
                    User(
                        email=email,
                        password_hash=hash_password(secrets.token_urlsafe(32)),
                        email_verified_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )

            subscription = None
            if external_subscription_id:
                stmt = select(ExternalSubscription).where(
                    ExternalSubscription.provider == provider,
                    ExternalSubscription.external_subscription_id == external_subscription_id,
                )
                subscription = session.execute(stmt).scalar_one_or_none()
            if not subscription:
                stmt = select(ExternalSubscription).where(
                    ExternalSubscription.provider == provider,
                    ExternalSubscription.external_customer_id == external_customer_id,
                )
                subscription = session.execute(stmt).scalar_one_or_none()
            if not subscription:
                subscription = ExternalSubscription(
                    id=str(uuid.uuid4()),
                    provider=provider,
                    external_customer_id=external_customer_id,
                    external_subscription_id=external_subscription_id,
                    external_order_id=external_order_id,
                    external_product_id=external_product_id,
                    external_plan_id=external_plan_id,
                    member_id=member.id,
                    status=subscription_status.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(subscription)
            else:
                subscription.external_subscription_id = external_subscription_id or subscription.external_subscription_id
                subscription.external_order_id = external_order_id or subscription.external_order_id
                subscription.external_product_id = external_product_id or subscription.external_product_id
                subscription.external_plan_id = external_plan_id or subscription.external_plan_id
                subscription.member_id = member.id
                subscription.status = subscription_status.value
                subscription.updated_at = now

            session.commit()
            session.refresh(member)
            session.refresh(subscription)
            return member, subscription

    def ensure_card_for_subscription(
        self,
        *,
        provider: str,
        external_subscription_id: str,
        external_product_id: str | None,
        owner_email: str | None,
        external_event_id: str,
    ) -> Card:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            card = self._find_subscription_card(
                session,
                provider=provider,
                external_subscription_id=external_subscription_id,
                external_product_id=external_product_id,
            )
            if card:
                return card

            uid = self._generate_uid(session)
            card = Card(
                uid=uid,
                status="pending",
                status_reason=CardStatusReason.WEBHOOK_CREATED.value,
                pin=self._generate_pin(),
                billing_status="ok",
                owner_email=owner_email,
                vanity=None,
                external_provider=provider,
                external_subscription_id=external_subscription_id,
                external_product_id=external_product_id,
                metrics_views=0,
                custom_domain_meta={},
                created_at=now,
                updated_at=now,
            )
            session.add(card)
            session.flush()
            self._add_history(
                session,
                card_uid=card.uid,
                previous_status=None,
                new_status=card.status,
                reason=CardStatusReason.WEBHOOK_CREATED.value,
                external_event_id=external_event_id,
                metadata={"external_product_id": external_product_id},
            )
            session.commit()
            session.refresh(card)
            return card

    def update_card_status_for_subscription(
        self,
        *,
        provider: str,
        external_subscription_id: str,
        external_product_id: str | None,
        new_status: str,
        reason: CardStatusReason,
        external_event_id: str,
        reactivate_only_payment_overdue: bool = False,
    ) -> Card | None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            card = self._find_subscription_card(
                session,
                provider=provider,
                external_subscription_id=external_subscription_id,
                external_product_id=external_product_id,
            )
            if not card:
                return None
            if reactivate_only_payment_overdue and not (
                card.status == "blocked" and card.status_reason == CardStatusReason.PAYMENT_OVERDUE.value
            ):
                return card
            previous = card.status
            if previous == new_status and card.status_reason == reason.value:
                return card
            card.status = new_status
            card.status_reason = reason.value
            if reason in {CardStatusReason.PAYMENT_APPROVED, CardStatusReason.PAYMENT_REGULARIZED}:
                card.billing_status = "ok"
            elif reason in {
                CardStatusReason.PAYMENT_OVERDUE,
                CardStatusReason.PAYMENT_REFUNDED,
                CardStatusReason.CHARGEBACK_RECEIVED,
                CardStatusReason.SUBSCRIPTION_CANCELLED,
            }:
                card.billing_status = "blocked"
            card.updated_at = now
            self._add_history(
                session,
                card_uid=card.uid,
                previous_status=previous,
                new_status=new_status,
                reason=reason.value,
                external_event_id=external_event_id,
                metadata={"external_product_id": external_product_id},
            )
            session.commit()
            session.refresh(card)
            return card

    @staticmethod
    def _find_subscription_card(session, *, provider: str, external_subscription_id: str, external_product_id: str | None):
        stmt = select(Card).where(
            Card.external_provider == provider,
            Card.external_subscription_id == external_subscription_id,
        )
        if external_product_id:
            stmt = stmt.where(
                or_(Card.external_product_id == external_product_id, Card.external_product_id.is_(None))
            )
        return session.execute(stmt.order_by(Card.created_at.asc()).limit(1)).scalar_one_or_none()

    @staticmethod
    def _generate_uid(session) -> str:
        for _ in range(20):
            uid = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8].lower()
            if not session.get(Card, uid):
                return uid
        return str(uuid.uuid4())[:12]

    @staticmethod
    def _generate_pin() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def _add_history(
        session,
        *,
        card_uid: str,
        previous_status: str | None,
        new_status: str,
        reason: str,
        external_event_id: str,
        metadata: dict | None = None,
    ) -> None:
        session.add(
            CardStatusHistory(
                id=str(uuid.uuid4()),
                card_uid=card_uid,
                previous_status=previous_status,
                new_status=new_status,
                reason=reason,
                source=CardStatusSource.WEBHOOK.value,
                actor_id=None,
                external_event_id=external_event_id,
                metadata_json=metadata or {},
                created_at=datetime.now(timezone.utc),
            )
        )
