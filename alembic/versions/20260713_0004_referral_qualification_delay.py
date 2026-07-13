"""Add delayed referral qualification.

Revision ID: 20260713_0004
Revises: 20260712_0003
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260713_0004"
down_revision = "20260712_0003"
branch_labels = None
depends_on = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, "referrals"):
        return
    if not _has_column(inspector, "referrals", "qualify_after"):
        op.add_column("referrals", sa.Column("qualify_after", sa.DateTime(timezone=True), nullable=True))
    inspector = sa.inspect(bind)
    if not _has_index(inspector, "referrals", "idx_referrals_pending_qualification"):
        op.create_index("idx_referrals_pending_qualification", "referrals", ["status", "qualify_after"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_index(inspector, "referrals", "idx_referrals_pending_qualification"):
        op.drop_index("idx_referrals_pending_qualification", table_name="referrals")
    inspector = sa.inspect(bind)
    if _has_column(inspector, "referrals", "qualify_after"):
        op.drop_column("referrals", "qualify_after")
