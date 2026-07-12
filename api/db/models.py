"""SQLAlchemy models mirroring the legacy JSON structures."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .session import Base


class User(Base):
    __tablename__ = "users"

    email = Column(String(255), primary_key=True)
    password_hash = Column(Text, nullable=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    cards = relationship("Card", back_populates="owner", cascade="all,delete-orphan")
    profile = relationship("Profile", uselist=False, back_populates="user", cascade="all,delete-orphan")


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint(
            "external_provider",
            "external_subscription_id",
            "external_product_id",
            name="uk_cards_external_subscription_product",
        ),
    )

    uid = Column(String(64), primary_key=True)
    status = Column(String(32), default="pending", nullable=False, index=True)
    status_reason = Column(String(60), nullable=True)
    pin = Column(String(32), nullable=False)
    billing_status = Column(String(32), nullable=True)
    owner_email = Column(String(255), ForeignKey("users.email", ondelete="SET NULL"), nullable=True, index=True)
    vanity = Column(String(64), unique=True, nullable=True)
    external_provider = Column(String(50), nullable=True, index=True)
    external_subscription_id = Column(String(150), nullable=True, index=True)
    external_product_id = Column(String(150), nullable=True)
    metrics_views = Column(Integer, default=0, nullable=False)
    custom_domain_meta = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    owner = relationship("User", back_populates="cards")
    custom_domain = relationship("CustomDomain", back_populates="card", uselist=False, cascade="all,delete-orphan")


class Profile(Base):
    __tablename__ = "profiles"

    email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), primary_key=True)
    data = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="profile")


class UserSession(Base):
    __tablename__ = "sessions"

    token = Column(String(128), primary_key=True)
    user_email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdminSession(Base):
    __tablename__ = "sessions_admin"

    token = Column(String(128), primary_key=True)
    email = Column(String(255), nullable=False)
    csrf_token = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VerifyToken(Base):
    __tablename__ = "verify_tokens"

    token = Column(String(255), primary_key=True)
    email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ResetToken(Base):
    __tablename__ = "reset_tokens"

    token = Column(String(255), primary_key=True)
    email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CustomDomain(Base):
    __tablename__ = "custom_domains"

    host = Column(String(255), primary_key=True)
    card_uid = Column(String(64), ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    card = relationship("Card", back_populates="custom_domain")


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("provider", "external_customer_id", name="uk_members_provider_customer"),
    )

    id = Column(String(36), primary_key=True)
    provider = Column(String(50), nullable=False, index=True)
    external_customer_id = Column(String(150), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    name = Column(String(255), nullable=True)
    phone = Column(String(40), nullable=True)
    document = Column(String(40), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    subscriptions = relationship("ExternalSubscription", back_populates="member")


class ExternalSubscription(Base):
    __tablename__ = "external_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "external_customer_id", name="uk_external_subscriptions_customer"),
        UniqueConstraint("provider", "external_subscription_id", name="uk_external_subscriptions_subscription"),
        Index("idx_external_subscriptions_customer", "provider", "external_customer_id"),
        Index("idx_external_subscriptions_subscription", "provider", "external_subscription_id"),
    )

    id = Column(String(36), primary_key=True)
    provider = Column(String(50), nullable=False)
    external_customer_id = Column(String(150), nullable=False)
    external_subscription_id = Column(String(150), nullable=True)
    external_order_id = Column(String(150), nullable=True)
    external_product_id = Column(String(150), nullable=True)
    external_plan_id = Column(String(150), nullable=True)
    member_id = Column(String(36), ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    member = relationship("Member", back_populates="subscriptions")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uk_webhook_event"),
        Index("idx_webhook_events_status_received_at", "status", "received_at"),
        Index("idx_webhook_events_retry", "status", "next_retry_at"),
    )

    id = Column(String(36), primary_key=True)
    provider = Column(String(50), nullable=False)
    external_event_id = Column(String(150), nullable=False)
    event_type = Column(String(100), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(30), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    correlation_id = Column(String(80), nullable=True, index=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)


class CardStatusHistory(Base):
    __tablename__ = "card_status_history"
    __table_args__ = (
        Index("idx_card_status_history_card_created", "card_uid", "created_at"),
    )

    id = Column(String(36), primary_key=True)
    card_uid = Column(String(64), ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False)
    previous_status = Column(String(40), nullable=True)
    new_status = Column(String(40), nullable=False)
    reason = Column(String(60), nullable=False)
    source = Column(String(40), nullable=False)
    actor_id = Column(String(150), nullable=True)
    external_event_id = Column(String(150), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
