"""Domain exceptions for membership webhook processing."""
from __future__ import annotations


class WebhookError(Exception):
    """Base class for webhook errors."""

    code = "webhook_error"
    retryable = False


class WebhookAuthenticationError(WebhookError):
    code = "invalid_authentication"


class WebhookPayloadError(WebhookError):
    code = "invalid_payload"


class WebhookPermanentError(WebhookError):
    code = "permanent_processing_error"


class WebhookRetryableError(WebhookError):
    code = "retryable_processing_error"
    retryable = True

