"""Application service for membership platform webhooks."""
from __future__ import annotations

import json
import logging
import uuid

from pydantic import ValidationError

from api.core.config import Settings, get_settings
from api.db.models import WebhookEvent

from .adapters import adapt_themembers_payload, is_themembers_provider
from .enums import WebhookEventStatus
from .exceptions import WebhookPayloadError, WebhookRetryableError
from .handlers import MembershipEventHandlers
from .repository import MembershipRepository, RegisteredWebhookEvent
from .schemas import MembershipWebhookPayload, payload_to_safe_dict

logger = logging.getLogger(__name__)


class MembershipWebhookService:
    """Validate, register and process webhook inbox events."""

    def __init__(
        self,
        *,
        repository: MembershipRepository | None = None,
        handlers: MembershipEventHandlers | None = None,
        settings: Settings | None = None,
    ):
        self.repository = repository or MembershipRepository()
        self.handlers = handlers or MembershipEventHandlers(self.repository)
        self.settings = settings or get_settings()

    def parse_payload(self, raw_body: bytes) -> MembershipWebhookPayload:
        payload_dict = self.load_raw_payload(raw_body)
        if is_themembers_provider(self.settings.membership_webhook_provider):
            payload_dict = adapt_themembers_payload(payload_dict)
        try:
            return MembershipWebhookPayload.model_validate(payload_dict)
        except ValidationError as exc:
            raise WebhookPayloadError("Invalid webhook payload.") from exc

    def load_raw_payload(self, raw_body: bytes) -> dict:
        if len(raw_body or b"") > int(self.settings.membership_webhook_max_payload_bytes or 1048576):
            raise WebhookPayloadError("Payload exceeds maximum size.")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadError("Malformed JSON payload.") from exc
        if not isinstance(payload, dict):
            raise WebhookPayloadError("Webhook payload must be an object.")
        return payload

    def register_event(
        self,
        *,
        raw_body: bytes,
        provider: str,
        header_event_id: str | None,
        correlation_id: str | None,
    ) -> RegisteredWebhookEvent:
        payload = self.parse_payload(raw_body)
        external_event_id = (header_event_id or payload.event_id or "").strip()
        if not external_event_id:
            raise WebhookPayloadError("event_id is required.")
        correlation = (correlation_id or "").strip() or str(uuid.uuid4())
        registered = self.repository.register_webhook_event(
            provider=provider,
            external_event_id=external_event_id,
            event_type=payload.event_type,
            payload=payload_to_safe_dict(payload),
            correlation_id=correlation,
        )
        logger.info(
            "membership_webhook_registered",
            extra={
                "provider": provider,
                "external_event_id": external_event_id,
                "event_type": payload.event_type,
                "duplicate": registered.duplicate,
                "correlation_id": correlation,
            },
        )
        return registered

    def process_event(self, event_id: str) -> WebhookEvent | None:
        event = self.repository.claim_event_for_processing(event_id)
        if not event:
            return self.repository.get_webhook_event(event_id)
        try:
            outcome = self.handlers.handle(event)
            if outcome == "ignored":
                self.repository.mark_event_ignored(
                    event.id,
                    code="unsupported_event_type",
                    message=f"Unsupported event_type: {event.event_type}",
                )
            else:
                self.repository.mark_event_processed(event.id)
        except WebhookRetryableError as exc:
            self.repository.mark_event_failed(
                event.id,
                code=exc.code,
                message=str(exc),
                retryable=True,
                max_retries=self.settings.membership_webhook_max_retries,
            )
        except Exception as exc:
            code = getattr(exc, "code", "processing_error")
            retryable = bool(getattr(exc, "retryable", False))
            self.repository.mark_event_failed(
                event.id,
                code=code,
                message=str(exc),
                retryable=retryable,
                max_retries=self.settings.membership_webhook_max_retries,
            )
            if retryable:
                return self.repository.get_webhook_event(event.id)
        return self.repository.get_webhook_event(event.id)

    def process_pending(self, *, limit: int | None = None) -> int:
        batch_size = limit or self.settings.membership_webhook_worker_batch_size
        events = self.repository.pending_webhook_events(limit=batch_size)
        processed = 0
        for event in events:
            self.process_event(event.id)
            processed += 1
        return processed
