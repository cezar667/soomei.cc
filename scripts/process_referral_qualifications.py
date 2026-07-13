#!/usr/bin/env python
"""Process pending referral qualifications.

Designed to run from cron/systemd timer once per day in production.
"""

from __future__ import annotations

import argparse

from api.referrals.service import ReferralService


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualifica/desqualifica indicações com janela de validação vencida.")
    parser.add_argument("--limit", type=int, default=None, help="Quantidade máxima de indicações processadas nesta execução.")
    args = parser.parse_args()

    result = ReferralService().process_due_qualifications(limit=args.limit)
    print(
        "referral_qualifications "
        f"processed={result['processed']} "
        f"qualified={result['qualified']} "
        f"disqualified={result['disqualified']}"
    )


if __name__ == "__main__":
    main()
