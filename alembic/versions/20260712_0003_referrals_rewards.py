"""Add referrals and rewards module.

Revision ID: 20260712_0003
Revises: 20260712_0002
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260712_0003"
down_revision = "20260712_0002"
branch_labels = None
depends_on = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_unique(inspector, table_name: str, constraint_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(c.get("name") == constraint_name for c in inspector.get_unique_constraints(table_name))


def _create_index_if_missing(inspector, name: str, table: str, columns: list[str]) -> None:
    if _has_table(inspector, table) and not _has_index(inspector, table, name):
        op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "referral_codes"):
        op.create_table(
            "referral_codes",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("owner_card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("owner_email", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("disabled_reason", sa.Text(), nullable=True),
            sa.UniqueConstraint("code", name="uk_referral_codes_code"),
        )
    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "idx_referral_codes_owner_card", "referral_codes", ["owner_card_uid"])
    _create_index_if_missing(inspector, "idx_referral_codes_owner_email", "referral_codes", ["owner_email"])
    _create_index_if_missing(inspector, "idx_referral_codes_status", "referral_codes", ["status"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "referrals"):
        op.create_table(
            "referrals",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("referral_code_id", sa.String(length=36), sa.ForeignKey("referral_codes.id", ondelete="SET NULL"), nullable=True),
            sa.Column("code_used", sa.String(length=40), nullable=False),
            sa.Column("referrer_card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="SET NULL"), nullable=True),
            sa.Column("referrer_email", sa.String(length=255), nullable=True),
            sa.Column("referred_card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("referred_email", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("qualify_after", sa.DateTime(timezone=True), nullable=True),
            sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="onboarding"),
            sa.Column("ip_address", sa.String(length=80), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("referred_card_uid", name="uk_referrals_referred_card"),
        )
    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "idx_referrals_referrer_card", "referrals", ["referrer_card_uid"])
    _create_index_if_missing(inspector, "idx_referrals_status_created", "referrals", ["status", "created_at"])
    _create_index_if_missing(inspector, "idx_referrals_pending_qualification", "referrals", ["status", "qualify_after"])
    _create_index_if_missing(inspector, "idx_referrals_code_used", "referrals", ["code_used"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "profile_badges"):
        op.create_table(
            "profile_badges",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("badge_type", sa.String(length=50), nullable=False),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=40), nullable=True),
            sa.Column("source_id", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("card_uid", "badge_type", name="uk_profile_badges_card_type"),
        )
    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "idx_profile_badges_card_expires", "profile_badges", ["card_uid", "expires_at"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "referral_campaigns"):
        op.create_table(
            "referral_campaigns",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rules_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("slug", name="uk_referral_campaigns_slug"),
        )
    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "idx_referral_campaigns_status", "referral_campaigns", ["status"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "referral_rewards"):
        op.create_table(
            "referral_rewards",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("referral_id", sa.String(length=36), sa.ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False),
            sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("referral_campaigns.id", ondelete="SET NULL"), nullable=True),
            sa.Column("beneficiary_card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("beneficiary_email", sa.String(length=255), nullable=True),
            sa.Column("reward_type", sa.String(length=40), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="granted"),
            sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("referral_id", "beneficiary_card_uid", "reward_type", "campaign_id", name="uk_referral_rewards_idempotency"),
        )
    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "idx_referral_rewards_beneficiary", "referral_rewards", ["beneficiary_card_uid", "created_at"])
    _create_index_if_missing(inspector, "idx_referral_rewards_type_status", "referral_rewards", ["reward_type", "status"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "raffle_entries"):
        op.create_table(
            "raffle_entries",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("campaign_id", sa.String(length=36), sa.ForeignKey("referral_campaigns.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reward_id", sa.String(length=36), sa.ForeignKey("referral_rewards.id", ondelete="CASCADE"), nullable=False),
            sa.Column("card_uid", sa.String(length=64), sa.ForeignKey("cards.uid", ondelete="CASCADE"), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("entry_code", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_reason", sa.Text(), nullable=True),
            sa.UniqueConstraint("entry_code", name="uk_raffle_entries_code"),
        )
    inspector = sa.inspect(bind)
    _create_index_if_missing(inspector, "idx_raffle_entries_campaign_card", "raffle_entries", ["campaign_id", "card_uid"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, index in [
        ("raffle_entries", "idx_raffle_entries_campaign_card"),
        ("referral_rewards", "idx_referral_rewards_type_status"),
        ("referral_rewards", "idx_referral_rewards_beneficiary"),
        ("referral_campaigns", "idx_referral_campaigns_status"),
        ("profile_badges", "idx_profile_badges_card_expires"),
        ("referrals", "idx_referrals_code_used"),
        ("referrals", "idx_referrals_pending_qualification"),
        ("referrals", "idx_referrals_status_created"),
        ("referrals", "idx_referrals_referrer_card"),
        ("referral_codes", "idx_referral_codes_status"),
        ("referral_codes", "idx_referral_codes_owner_email"),
        ("referral_codes", "idx_referral_codes_owner_card"),
    ]:
        if _has_index(inspector, table, index):
            op.drop_index(index, table_name=table)
            inspector = sa.inspect(bind)
    for table in ["raffle_entries", "referral_rewards", "referral_campaigns", "profile_badges", "referrals", "referral_codes"]:
        if _has_table(inspector, table):
            op.drop_table(table)
            inspector = sa.inspect(bind)
