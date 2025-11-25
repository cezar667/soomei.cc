"""SQLAlchemy models mirroring the legacy JSON structures."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    func,
)
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

    uid = Column(String(64), primary_key=True)
    status = Column(String(32), default="pending", nullable=False)
    pin = Column(String(32), nullable=False)
    billing_status = Column(String(32), nullable=True)
    owner_email = Column(String(255), ForeignKey("users.email", ondelete="SET NULL"), nullable=True)
    vanity = Column(String(64), unique=True, nullable=True)
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
    user_email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdminSession(Base):
    __tablename__ = "sessions_admin"

    token = Column(String(128), primary_key=True)
    email = Column(String(255), nullable=False)
    csrf_token = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VerifyToken(Base):
    __tablename__ = "verify_tokens"

    token = Column(String(255), primary_key=True)
    email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ResetToken(Base):
    __tablename__ = "reset_tokens"

    token = Column(String(255), primary_key=True)
    email = Column(String(255), ForeignKey("users.email", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CustomDomain(Base):
    __tablename__ = "custom_domains"

    host = Column(String(255), primary_key=True)
    card_uid = Column(String(64), ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    card = relationship("Card", back_populates="custom_domain")
