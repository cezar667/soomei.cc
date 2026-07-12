"""Provider-specific payload adapters for membership webhooks."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from .exceptions import WebhookAuthenticationError, WebhookPayloadError


THEMEMBERS_PROVIDER = "themembers"


THEMEMBERS_EVENT_MAP: dict[str, str] = {
    # Transaction events from TheMembers checkout.
    "transaction.approved": "subscription.payment_approved",
    "transaction.paid": "subscription.payment_approved",
    "transaction.failed": "subscription.payment_failed",
    "transaction.denied": "subscription.payment_failed",
    "transaction.refunded": "payment.refunded",
    "transaction.refund_pending": "payment.refund_pending",
    "transaction.chargeback": "payment.chargeback",
    "transaction.pix_created": "transaction.pix_created",
    "transaction.boleto_created": "transaction.boleto_created",
    "transaction.credit_card_started": "transaction.credit_card_started",
    # Order/sale events.
    "order.cancelled": "subscription.cancelled",
    "order.canceled": "subscription.cancelled",
    "order.expired": "subscription.overdue",
    "order.completed": "order.completed",
    # Access events. These are generally the safest business hooks.
    "access.granted": "subscription.payment_approved",
    "access.released": "subscription.payment_approved",
    "access.removed": "subscription.cancelled",
    "access.revoked": "subscription.cancelled",
    # Cart abandonment.
    "checkout.abandoned": "checkout.abandoned",
    "cart.abandoned": "checkout.abandoned",
}


def is_themembers_provider(provider: str) -> bool:
    return (provider or "").strip().lower() in {THEMEMBERS_PROVIDER, "the_members"}


def validate_themembers_payload_token(payload: dict[str, Any], *, secrets_list: list[str]) -> None:
    """Validate TheMembers payload token using constant-time comparison."""
    configured = [value for value in secrets_list if value]
    if not configured:
        raise WebhookAuthenticationError("Webhook token is not configured.")
    received = _extract_payload_token(payload)
    if not received:
        raise WebhookAuthenticationError("Invalid webhook authentication.")
    for secret in configured:
        if secrets.compare_digest(secret, received):
            return
    raise WebhookAuthenticationError("Invalid webhook authentication.")


def adapt_themembers_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize TheMembers' native webhook payload to the internal Soomei contract."""
    if not isinstance(payload, dict):
        raise WebhookPayloadError("Invalid TheMembers payload.")
    data = _dict(payload.get("data"))
    event_id = _first_text(payload.get("id"), payload.get("event_id"), data.get("webhook_id"))
    native_event = _first_text(payload.get("event"), payload.get("event_type"), data.get("event"))
    created_at = _first_text(payload.get("created_at"), data.get("created_at")) or _now_iso()
    if not event_id:
        raise WebhookPayloadError("TheMembers payload id is required.")
    if not native_event:
        raise WebhookPayloadError("TheMembers payload event is required.")

    customer = _extract_customer(data)
    customer_email = _first_text(customer.get("email"), data.get("email"), payload.get("email"))
    customer_id = _first_text(
        customer.get("id"),
        data.get("customer_id"),
        data.get("buyer_id"),
        data.get("client_id"),
        data.get("user_id"),
    )
    if not customer_id and customer_email:
        customer_id = f"email:{customer_email.strip().lower()}"

    transaction_id = _first_text(data.get("id"), data.get("transaction_id"), data.get("txn_id"))
    order = _dict(data.get("order"))
    subscription = _dict(data.get("subscription"))
    product = _extract_product(data)
    plan = _dict(data.get("plan"))

    order_id = _first_text(order.get("id"), data.get("order_id"), payload.get("order_id"), transaction_id)
    subscription_id = _first_text(
        subscription.get("id"),
        data.get("subscription_id"),
        data.get("access_id"),
        order_id,
        transaction_id,
    )
    product_id = _first_text(product.get("id"), product.get("product_id"), data.get("product_id"))
    plan_id = _first_text(plan.get("id"), data.get("plan_id"))

    if not customer_id:
        customer_id = _first_text(subscription_id, order_id, transaction_id, event_id)

    return {
        "event_id": event_id,
        "event_type": THEMEMBERS_EVENT_MAP.get(native_event, native_event),
        "created_at": created_at,
        "data": {
            "customer_id": customer_id,
            "subscription_id": subscription_id,
            "order_id": order_id,
            "product_id": product_id,
            "plan_id": plan_id,
            "customer": {
                "name": _first_text(
                    customer.get("name"),
                    customer.get("full_name"),
                    data.get("name"),
                    data.get("customer_name"),
                    data.get("buyer_name"),
                ),
                "email": customer_email,
                "phone": _first_text(customer.get("phone"), data.get("phone"), data.get("customer_phone")),
                "document": _first_text(
                    customer.get("document"),
                    customer.get("cpf"),
                    customer.get("cnpj"),
                    data.get("document"),
                    data.get("customer_document"),
                ),
            },
            "native_provider": THEMEMBERS_PROVIDER,
            "native_event": native_event,
            "native_object": _first_text(payload.get("object")),
            "transaction_id": transaction_id,
        },
    }


def _extract_payload_token(payload: dict[str, Any]) -> str:
    data = _dict(payload.get("data"))
    candidates = [
        payload.get("token"),
        payload.get("security_token"),
        payload.get("webhook_token"),
        payload.get("secret"),
        data.get("token"),
        data.get("security_token"),
        data.get("webhook_token"),
    ]
    return _first_text(*candidates)


def _extract_customer(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("customer", "buyer", "client", "user", "student", "contact"):
        value = _dict(data.get(key))
        if value:
            return value
    return {}


def _extract_product(data: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(data.get("product"))
    if direct:
        return direct
    products = data.get("products")
    if isinstance(products, list) and products:
        return _dict(products[0])
    return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

