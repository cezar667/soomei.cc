"""FastAPI router for membership platform webhooks."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from api.core.config import get_settings
from api.core.rate_limiter import rate_limit_ip

from .adapters import is_themembers_provider, validate_themembers_payload_token
from .exceptions import WebhookAuthenticationError, WebhookPayloadError
from .service import MembershipWebhookService
from .signature import validate_webhook_signature

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


@router.post("/membership-platform", status_code=status.HTTP_200_OK)
async def receive_membership_webhook(
    request: Request,
    x_webhook_timestamp: str | None = Header(default=None),
    x_webhook_signature: str | None = Header(default=None),
    x_webhook_event_id: str | None = Header(default=None),
    x_correlation_id: str | None = Header(default=None),
):
    settings = get_settings()
    if not settings.membership_webhook_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    if settings.app_env == "prod" and not _is_secure_request(request):
        raise HTTPException(status_code=403, detail="HTTPS required")

    rate_limit_ip(
        request,
        "membership:webhook",
        limit=settings.membership_webhook_rate_limit_per_minute,
        window_seconds=60,
    )

    raw_body = await request.body()
    service = MembershipWebhookService(settings=settings)
    if is_themembers_provider(settings.membership_webhook_provider):
        try:
            raw_payload = service.load_raw_payload(raw_body)
            validate_themembers_payload_token(
                raw_payload,
                secrets_list=[settings.membership_webhook_secret, settings.membership_webhook_previous_secret],
            )
        except WebhookPayloadError as exc:
            raise HTTPException(status_code=422, detail="Invalid webhook payload") from exc
        except WebhookAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="Invalid webhook authentication") from exc
    else:
        try:
            validate_webhook_signature(
                secrets=[settings.membership_webhook_secret, settings.membership_webhook_previous_secret],
                timestamp=x_webhook_timestamp or "",
                received_signature=x_webhook_signature or "",
                raw_body=raw_body,
                max_delay_seconds=settings.membership_webhook_max_delay_seconds,
            )
        except WebhookAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="Invalid webhook authentication") from exc

    try:
        registered = service.register_event(
            raw_body=raw_body,
            provider=settings.membership_webhook_provider,
            header_event_id=x_webhook_event_id,
            correlation_id=x_correlation_id,
        )
    except WebhookPayloadError as exc:
        raise HTTPException(status_code=422, detail="Invalid webhook payload") from exc

    if not registered.duplicate:
        service.process_event(registered.event.id)

    return {
        "received": True,
        "event_id": registered.event.external_event_id,
        "duplicate": registered.duplicate,
    }
