"""Business event handlers for the membership platform integration."""
from __future__ import annotations

from api.db.models import WebhookEvent
from api.referrals.repository import ReferralRepository

from .enums import CardStatusReason, InternalEventType, SubscriptionStatus, SUPPORTED_EVENT_MAP
from .exceptions import WebhookPermanentError
from .repository import MembershipRepository


class MembershipEventHandlers:
    """Translate external business events into Soomei domain changes."""

    def __init__(self, repository: MembershipRepository | None = None):
        self.repository = repository or MembershipRepository()
        self.referral_repository = ReferralRepository()

    def handle(self, event: WebhookEvent) -> str:
        internal_event = SUPPORTED_EVENT_MAP.get(event.event_type, InternalEventType.UNSUPPORTED)
        if internal_event == InternalEventType.UNSUPPORTED:
            return "ignored"

        payload = event.payload or {}
        data = payload.get("data") or {}
        external_customer_id = (data.get("customer_id") or "").strip()
        external_subscription_id = (data.get("subscription_id") or "").strip() or None
        external_order_id = (data.get("order_id") or "").strip() or None
        external_product_id = (data.get("product_id") or "").strip() or None
        external_plan_id = (data.get("plan_id") or "").strip() or None
        customer = data.get("customer") or {}

        if not external_customer_id:
            raise WebhookPermanentError("customer_id is required.")

        if internal_event in {
            InternalEventType.SUBSCRIPTION_CREATED,
            InternalEventType.PAYMENT_APPROVED,
            InternalEventType.PAYMENT_FAILED,
            InternalEventType.SUBSCRIPTION_OVERDUE,
            InternalEventType.SUBSCRIPTION_REACTIVATED,
            InternalEventType.SUBSCRIPTION_CANCELLED,
            InternalEventType.PAYMENT_REFUNDED,
            InternalEventType.CHARGEBACK_RECEIVED,
        } and not external_subscription_id:
            raise WebhookPermanentError("subscription_id is required for subscription events.")

        if internal_event == InternalEventType.CUSTOMER_CREATED:
            self.repository.upsert_member_and_subscription(
                provider=event.provider,
                external_customer_id=external_customer_id,
                external_subscription_id=external_subscription_id,
                external_order_id=external_order_id,
                external_product_id=external_product_id,
                external_plan_id=external_plan_id,
                customer=customer,
                subscription_status=SubscriptionStatus.PENDING,
            )
            return "processed"

        subscription_status = self._subscription_status_for_event(internal_event)
        member, subscription = self.repository.upsert_member_and_subscription(
            provider=event.provider,
            external_customer_id=external_customer_id,
            external_subscription_id=external_subscription_id,
            external_order_id=external_order_id,
            external_product_id=external_product_id,
            external_plan_id=external_plan_id,
            customer=customer,
            subscription_status=subscription_status,
        )

        owner_email = (member.email or "").strip().lower() or None

        if internal_event == InternalEventType.SUBSCRIPTION_CREATED:
            return "processed"

        if internal_event == InternalEventType.PAYMENT_APPROVED:
            self.repository.ensure_card_for_subscription(
                provider=event.provider,
                external_subscription_id=subscription.external_subscription_id or external_subscription_id or "",
                external_product_id=subscription.external_product_id or external_product_id,
                owner_email=owner_email,
                external_event_id=event.external_event_id,
            )
            return "processed"

        if internal_event == InternalEventType.PAYMENT_FAILED:
            return "processed"

        if internal_event == InternalEventType.SUBSCRIPTION_OVERDUE:
            card = self.repository.update_card_status_for_subscription(
                provider=event.provider,
                external_subscription_id=subscription.external_subscription_id or external_subscription_id or "",
                external_product_id=subscription.external_product_id or external_product_id,
                new_status="blocked",
                reason=CardStatusReason.PAYMENT_OVERDUE,
                external_event_id=event.external_event_id,
            )
            self._disqualify_pending_referral(card, reason=CardStatusReason.PAYMENT_OVERDUE.value)
            return "processed"

        if internal_event == InternalEventType.SUBSCRIPTION_REACTIVATED:
            self.repository.update_card_status_for_subscription(
                provider=event.provider,
                external_subscription_id=subscription.external_subscription_id or external_subscription_id or "",
                external_product_id=subscription.external_product_id or external_product_id,
                new_status="active",
                reason=CardStatusReason.PAYMENT_REGULARIZED,
                external_event_id=event.external_event_id,
                reactivate_only_payment_overdue=True,
            )
            return "processed"

        if internal_event == InternalEventType.SUBSCRIPTION_CANCELLED:
            card = self.repository.update_card_status_for_subscription(
                provider=event.provider,
                external_subscription_id=subscription.external_subscription_id or external_subscription_id or "",
                external_product_id=subscription.external_product_id or external_product_id,
                new_status="blocked",
                reason=CardStatusReason.SUBSCRIPTION_CANCELLED,
                external_event_id=event.external_event_id,
            )
            self._disqualify_pending_referral(card, reason=CardStatusReason.SUBSCRIPTION_CANCELLED.value)
            return "processed"

        if internal_event == InternalEventType.PAYMENT_REFUNDED:
            card = self.repository.update_card_status_for_subscription(
                provider=event.provider,
                external_subscription_id=subscription.external_subscription_id or external_subscription_id or "",
                external_product_id=subscription.external_product_id or external_product_id,
                new_status="blocked",
                reason=CardStatusReason.PAYMENT_REFUNDED,
                external_event_id=event.external_event_id,
            )
            self._disqualify_pending_referral(card, reason=CardStatusReason.PAYMENT_REFUNDED.value)
            return "processed"

        if internal_event == InternalEventType.CHARGEBACK_RECEIVED:
            card = self.repository.update_card_status_for_subscription(
                provider=event.provider,
                external_subscription_id=subscription.external_subscription_id or external_subscription_id or "",
                external_product_id=subscription.external_product_id or external_product_id,
                new_status="blocked",
                reason=CardStatusReason.CHARGEBACK_RECEIVED,
                external_event_id=event.external_event_id,
            )
            self._disqualify_pending_referral(card, reason=CardStatusReason.CHARGEBACK_RECEIVED.value)
            return "processed"

        return "ignored"

    def _disqualify_pending_referral(self, card, *, reason: str) -> None:
        if not card:
            return
        self.referral_repository.disqualify_pending_for_referred_card(
            referred_card_uid=card.uid,
            reason=reason,
        )

    @staticmethod
    def _subscription_status_for_event(event_type: InternalEventType) -> SubscriptionStatus:
        if event_type in {InternalEventType.PAYMENT_APPROVED, InternalEventType.SUBSCRIPTION_REACTIVATED}:
            return SubscriptionStatus.ACTIVE
        if event_type in {InternalEventType.PAYMENT_FAILED, InternalEventType.SUBSCRIPTION_OVERDUE}:
            return SubscriptionStatus.OVERDUE
        if event_type == InternalEventType.SUBSCRIPTION_CANCELLED:
            return SubscriptionStatus.CANCELLED
        if event_type == InternalEventType.PAYMENT_REFUNDED:
            return SubscriptionStatus.REFUNDED
        if event_type == InternalEventType.CHARGEBACK_RECEIVED:
            return SubscriptionStatus.SUSPENDED
        return SubscriptionStatus.PENDING
