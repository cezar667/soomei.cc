"""Add referral job run audit table.

Revision ID: 20260713_0005
Revises: 20260713_0004
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260713_0005"
down_revision = "20260713_0004"
branch_labels = None
depends_on = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, "referral_job_runs"):
        op.create_table(
            "referral_job_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("job_name", sa.String(length=80), nullable=False),
            sa.Column("trigger", sa.String(length=40), nullable=False, server_default="systemd"),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("qualified_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("disqualified_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
    inspector = sa.inspect(bind)
    if not _has_index(inspector, "referral_job_runs", "idx_referral_job_runs_job_started"):
        op.create_index("idx_referral_job_runs_job_started", "referral_job_runs", ["job_name", "started_at"])
    if not _has_index(inspector, "referral_job_runs", "idx_referral_job_runs_status_started"):
        op.create_index("idx_referral_job_runs_status_started", "referral_job_runs", ["status", "started_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_index(inspector, "referral_job_runs", "idx_referral_job_runs_status_started"):
        op.drop_index("idx_referral_job_runs_status_started", table_name="referral_job_runs")
    inspector = sa.inspect(bind)
    if _has_index(inspector, "referral_job_runs", "idx_referral_job_runs_job_started"):
        op.drop_index("idx_referral_job_runs_job_started", table_name="referral_job_runs")
    inspector = sa.inspect(bind)
    if _has_table(inspector, "referral_job_runs"):
        op.drop_table("referral_job_runs")
