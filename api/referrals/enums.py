from __future__ import annotations

from enum import StrEnum


class ReferralCodeStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ReferralStatus(StrEnum):
    PENDING_VALIDATION = "pending_validation"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    REJECTED = "rejected"


class BadgeType(StrEnum):
    SOOMEI_CONNECTOR = "soomei_connector"


class RewardType(StrEnum):
    BADGE_DAYS = "badge_days"
    RAFFLE_COUPON = "raffle_coupon"


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
