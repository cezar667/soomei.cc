"""Add membership webhook integration tables.

Revision ID: 20260712_0002
Revises: 20260505_0001
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260712_0002"
down_revision = "20260505_0001"
branch_labels = None
depends_on = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_index_on_columns(inspector, table_name: str, columns: list[str]) -> bool:
    if not _has_table(inspector, table_name):
        return False
    expected = tuple(columns)
    return any(tuple(index.get("column_names") or []) == expected for index in inspector.get_indexes(table_name))


def _has_unique_constraint(inspector, table_name: str, constraint_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(
        constraint.get("name") == constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
    )


def _has_unique_or_index(inspector, table_name: str, name: str) -> bool:
    return _has_unique_constraint(inspector, table_name, name) or _has_index(inspector, table_name, name)


def _create_index_if_missing(
    inspector,
    index_name: str,
    table_name: str,
    columns: list[str],
    *,
    accept_equivalent: bool = True,
    **kwargs,
) -> None:
    if not _has_table(inspector, table_name) or _has_index(inspector, table_name, index_name):
        return
    if accept_equivalent and _has_index_on_columns(inspector, table_name, columns):
        return
    else:
        op.create_index(index_name, table_name, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "cards"):
        card_columns = [
            ("status_reason", sa.Column("status_reason", sa.String(length=60), nullable=True)),
            ("external_provider", sa.Column("external_provider", sa.String(length=50), nullable=True)),
            (
                "external_subscription_id",
                sa.Column("external_subscription_id", sa.String(length=150), nullable=True),
            ),
            ("external_product_id", sa.Column("external_product_id", sa.String(length=150), nullable=True)),
        ]
        for column_name, column in card_columns:
            if not _has_column(inspector, "cards", column_name):
                op.add_column("cards", column)

        inspector = sa.inspect(bind)
        if not _has_unique_or_index(inspector, "cards", "uk_cards_external_subscription_product"):
            op.create_unique_constraint(
                "uk_cards_external_subscription_product",
                "cards",
                ["external_provider", "external_subscription_id", "external_product_id"],
            )
        inspector = sa.inspect(bind)
        _create_index_if_missing(inspector, "ix_cards_external_provider", "cards", ["external_provider"])
        _create_index_if_missing(
            inspector,
            "ix_cards_external_subscription_id",
            "cards",
            ["external_subscription_id"],
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "members"):
        op.create_table(
            "members",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("external_customer_id", sa.String(length=150), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("phone", sa.String(length=40), nullable=True),
            sa.Column("document", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider", "external_customer_id", name="uk_members_provider_customer"),
        )

    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "ix_members_provider", "members", ["provider"])
    _create_index_if_missing(inspector, "ix_members_email", "members", ["email"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "external_subscriptions"):
        op.create_table(
            "external_subscriptions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("external_customer_id", sa.String(length=150), nullable=False),
            sa.Column("external_subscription_id", sa.String(length=150), nullable=True),
            sa.Column("external_order_id", sa.String(length=150), nullable=True),
            sa.Column("external_product_id", sa.String(length=150), nullable=True),
            sa.Column("external_plan_id", sa.String(length=150), nullable=True),
            sa.Column("member_id", sa.String(length=36), sa.ForeignKey("members.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider", "external_customer_id", name="uk_external_subscriptions_customer"),
            sa.UniqueConstraint("provider", "external_subscription_id", name="uk_external_subscriptions_subscription"),
        )

    inspector = sa.inspect(bind)
    _create_index_if_missing(
        inspector,
        "idx_external_subscriptions_customer",
        "external_subscriptions",
        ["provider", "external_customer_id"],
    )
    _create_index_if_missing(
        inspector,
        "idx_external_subscriptions_subscription",
        "external_subscriptions",
        ["provider", "external_subscription_id"],
    )
    _create_index_if_missing(
        inspector,
        "ix_external_subscriptions_member_id",
        "external_subscriptions",
        ["member_id"],
    )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "webhook_events"):
        op.create_table(
            "webhook_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("external_event_id", sa.String(length=150), nullable=False),
            sa.Column("event_type", sa.String(length=100), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("correlation_id", sa.String(length=80), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_code", sa.String(length=100), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.UniqueConstraint("provider", "external_event_id", name="uk_webhook_event"),
        )

    inspector = sa.inspect(bind)
    _create_index_if_missing(
        inspector,
        "idx_webhook_events_status_received_at",
        "webhook_events",
        ["status", "received_at"],
    )
    _create_index_if_missing(inspector, "idx_webhook_events_retry", "webhook_events", ["status", "next_retry_at"])
    _create_index_if_missing(
        inspector,
        "ix_webhook_events_correlation_id",
        "webhook_events",
        ["correlation_id"],
    )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "card_status_history"):
        op.create_table(
            "card_status_history",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("previous_status", sa.String(length=40), nullable=True),
            sa.Column("new_status", sa.String(length=40), nullable=False),
            sa.Column("reason", sa.String(length=60), nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False),
            sa.Column("actor_id", sa.String(length=150), nullable=True),
            sa.Column("external_event_id", sa.String(length=150), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    inspector = sa.inspect(bind)
    _create_index_if_missing(
        inspector,
        "idx_card_status_history_card_created",
        "card_status_history",
        ["card_uid", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    indexes = [
        ("card_status_history", "idx_card_status_history_card_created"),
        ("webhook_events", "ix_webhook_events_correlation_id"),
        ("webhook_events", "idx_webhook_events_retry"),
        ("webhook_events", "idx_webhook_events_status_received_at"),
        ("external_subscriptions", "ix_external_subscriptions_member_id"),
        ("external_subscriptions", "idx_external_subscriptions_subscription"),
        ("external_subscriptions", "idx_external_subscriptions_customer"),
        ("members", "ix_members_email"),
        ("members", "ix_members_provider"),
        ("cards", "ix_cards_external_subscription_id"),
        ("cards", "ix_cards_external_provider"),
        ("cards", "idx_cards_external_subscription"),
        ("cards", "idx_cards_external_provider"),
    ]
    for table_name, index_name in indexes:
        if _has_index(inspector, table_name, index_name):
            op.drop_index(index_name, table_name=table_name)

    inspector = sa.inspect(bind)
    for table_name, constraint_name in [
        ("cards", "uk_cards_external_subscription_product"),
    ]:
        if _has_unique_constraint(inspector, table_name, constraint_name):
            op.drop_constraint(constraint_name, table_name, type_="unique")
        elif _has_index(inspector, table_name, constraint_name):
            op.drop_index(constraint_name, table_name=table_name)

    inspector = sa.inspect(bind)
    for table_name in ["card_status_history", "webhook_events", "external_subscriptions", "members"]:
        if _has_table(inspector, table_name):
            op.drop_table(table_name)
            inspector = sa.inspect(bind)

    inspector = sa.inspect(bind)
    if _has_table(inspector, "cards"):
        for column_name in [
            "external_product_id",
            "external_subscription_id",
            "external_provider",
            "status_reason",
        ]:
            if _has_column(inspector, "cards", column_name):
                op.drop_column("cards", column_name)
                inspector = sa.inspect(bind)
