"""Baseline schema and indexes for the SQL backend."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260505_0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "users"):
        op.create_table(
            "users",
            sa.Column("email", sa.String(length=255), primary_key=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "cards"):
        op.create_table(
            "cards",
            sa.Column("uid", sa.String(length=64), primary_key=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("pin", sa.String(length=32), nullable=False),
            sa.Column("billing_status", sa.String(length=32), nullable=True),
            sa.Column("owner_email", sa.String(length=255), sa.ForeignKey("users.email", ondelete="SET NULL"), nullable=True),
            sa.Column("vanity", sa.String(length=64), nullable=True, unique=True),
            sa.Column("metrics_views", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("custom_domain_meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "profiles"):
        op.create_table(
            "profiles",
            sa.Column("email", sa.String(length=255), sa.ForeignKey("users.email", ondelete="CASCADE"), primary_key=True),
            sa.Column("data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "sessions"):
        op.create_table(
            "sessions",
            sa.Column("token", sa.String(length=128), primary_key=True),
            sa.Column("user_email", sa.String(length=255), sa.ForeignKey("users.email", ondelete="CASCADE"), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "sessions_admin"):
        op.create_table(
            "sessions_admin",
            sa.Column("token", sa.String(length=128), primary_key=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("csrf_token", sa.String(length=255), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "verify_tokens"):
        op.create_table(
            "verify_tokens",
            sa.Column("token", sa.String(length=255), primary_key=True),
            sa.Column("email", sa.String(length=255), sa.ForeignKey("users.email", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "reset_tokens"):
        op.create_table(
            "reset_tokens",
            sa.Column("token", sa.String(length=255), primary_key=True),
            sa.Column("email", sa.String(length=255), sa.ForeignKey("users.email", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if not _has_table(inspector, "custom_domains"):
        op.create_table(
            "custom_domains",
            sa.Column("host", sa.String(length=255), primary_key=True),
            sa.Column("card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    inspector = sa.inspect(bind)
    indexes = {
        "cards": [
            ("ix_cards_status", ["status"]),
            ("ix_cards_owner_email", ["owner_email"]),
        ],
        "sessions": [
            ("ix_sessions_user_email", ["user_email"]),
            ("ix_sessions_expires_at", ["expires_at"]),
        ],
        "sessions_admin": [
            ("ix_sessions_admin_expires_at", ["expires_at"]),
        ],
        "verify_tokens": [
            ("ix_verify_tokens_email", ["email"]),
        ],
        "reset_tokens": [
            ("ix_reset_tokens_email", ["email"]),
        ],
        "custom_domains": [
            ("ix_custom_domains_card_uid", ["card_uid"]),
        ],
    }
    for table_name, table_indexes in indexes.items():
        if not _has_table(inspector, table_name):
            continue
        for index_name, columns in table_indexes:
            if not _has_index(inspector, table_name, index_name):
                op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = [
        ("custom_domains", "ix_custom_domains_card_uid"),
        ("reset_tokens", "ix_reset_tokens_email"),
        ("verify_tokens", "ix_verify_tokens_email"),
        ("sessions_admin", "ix_sessions_admin_expires_at"),
        ("sessions", "ix_sessions_expires_at"),
        ("sessions", "ix_sessions_user_email"),
        ("cards", "ix_cards_owner_email"),
        ("cards", "ix_cards_status"),
    ]
    for table_name, index_name in indexes:
        if _has_table(inspector, table_name) and _has_index(inspector, table_name, index_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in [
        "custom_domains",
        "reset_tokens",
        "verify_tokens",
        "sessions_admin",
        "sessions",
        "profiles",
        "cards",
        "users",
    ]:
        if _has_table(inspector, table_name):
            op.drop_table(table_name)
