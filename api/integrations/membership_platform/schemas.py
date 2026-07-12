"""Pydantic schemas for membership platform webhooks."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class WebhookCustomer(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    document: str | None = Field(default=None, max_length=40)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None


class WebhookData(BaseModel):
    customer_id: str = Field(min_length=1, max_length=150)
    subscription_id: str | None = Field(default=None, max_length=150)
    order_id: str | None = Field(default=None, max_length=150)
    product_id: str | None = Field(default=None, max_length=150)
    plan_id: str | None = Field(default=None, max_length=150)
    customer: WebhookCustomer | None = None
    native_provider: str | None = Field(default=None, max_length=50)
    native_event: str | None = Field(default=None, max_length=100)
    native_object: str | None = Field(default=None, max_length=100)
    transaction_id: str | None = Field(default=None, max_length=150)

    @field_validator(
        "customer_id",
        "subscription_id",
        "order_id",
        "product_id",
        "plan_id",
        "native_provider",
        "native_event",
        "native_object",
        "transaction_id",
    )
    @classmethod
    def strip_ids(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class MembershipWebhookPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=150)
    event_type: str = Field(min_length=1, max_length=100)
    created_at: datetime
    data: WebhookData

    @field_validator("event_id", "event_type")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        return value.strip()


def payload_to_safe_dict(payload: MembershipWebhookPayload) -> dict[str, Any]:
    """Serialize payload to a DB-friendly dict without reusing raw body for signing."""
    return payload.model_dump(mode="json")
