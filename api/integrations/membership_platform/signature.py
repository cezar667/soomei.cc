"""HMAC validation for membership platform webhooks."""
from __future__ import annotations

import hashlib
import hmac
import time

from .exceptions import WebhookAuthenticationError


SIGNATURE_PREFIX = "sha256="


def _normalize_signature(value: str) -> str:
    return (value or "").strip().removeprefix(SIGNATURE_PREFIX).strip()


def validate_webhook_signature(
    *,
    secrets: list[str],
    timestamp: str,
    received_signature: str,
    raw_body: bytes,
    max_delay_seconds: int = 300,
) -> None:
    """Validate HMAC-SHA256(timestamp + "." + raw_body) with constant-time comparison."""
    active_secrets = [secret for secret in secrets if secret]
    if not active_secrets:
        raise WebhookAuthenticationError("Webhook secret is not configured.")
    try:
        timestamp_value = int((timestamp or "").strip())
    except (TypeError, ValueError) as exc:
        raise WebhookAuthenticationError("Invalid webhook authentication.") from exc

    current_timestamp = int(time.time())
    if abs(current_timestamp - timestamp_value) > int(max_delay_seconds or 300):
        raise WebhookAuthenticationError("Invalid webhook authentication.")

    received = _normalize_signature(received_signature)
    if not received:
        raise WebhookAuthenticationError("Invalid webhook authentication.")

    signed_payload = timestamp.strip().encode("utf-8") + b"." + raw_body
    for secret in active_secrets:
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, received):
            return

    raise WebhookAuthenticationError("Invalid webhook authentication.")


def build_test_signature(*, secret: str, timestamp: str, raw_body: bytes) -> str:
    """Helper used by tests and local webhook documentation examples."""
    signed_payload = timestamp.strip().encode("utf-8") + b"." + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"

