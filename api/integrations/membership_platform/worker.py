"""Database-backed worker for pending membership webhook events."""
from __future__ import annotations

from api.core.config import get_settings

from .service import MembershipWebhookService


def process_pending_membership_webhooks(limit: int | None = None) -> int:
    """Process pending webhook events from the database inbox."""
    settings = get_settings()
    service = MembershipWebhookService(settings=settings)
    return service.process_pending(limit=limit)


if __name__ == "__main__":
    total = process_pending_membership_webhooks()
    print(f"Processed {total} membership webhook event(s).")

