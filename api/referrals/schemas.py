from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReferralSummary:
    code: str
    badge_expires_at: datetime | None
    badge_days_remaining: int
    qualified_referrals: int
    pending_referrals: int
    next_qualification_at: datetime | None
    raffle_coupons: int
    share_message: str


@dataclass(frozen=True)
class ReferralApplicationResult:
    applied: bool
    message: str = ""
    referral_id: str | None = None
