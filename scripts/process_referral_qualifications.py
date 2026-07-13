#!/usr/bin/env python
"""Process pending referral qualifications.

Designed to run from cron/systemd timer once per day in production.
"""

from __future__ import annotations

import argparse
import traceback

from api.referrals.repository import ReferralRepository
from api.referrals.service import ReferralService


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualifica/desqualifica indicações com janela de validação vencida.")
    parser.add_argument("--limit", type=int, default=None, help="Quantidade máxima de indicações processadas nesta execução.")
    parser.add_argument("--trigger", default="systemd", choices=["systemd", "manual", "cron"], help="Origem da execução registrada na auditoria.")
    args = parser.parse_args()

    repository = ReferralRepository()
    job_run = repository.start_job_run(trigger=args.trigger)
    try:
        result = ReferralService(repository=repository).process_due_qualifications(limit=args.limit)
        repository.finish_job_run(job_run.id, result=result)
    except Exception as exc:
        repository.fail_job_run(job_run.id, error_message="".join(traceback.format_exception_only(type(exc), exc)).strip())
        raise
    print(
        "referral_qualifications "
        f"run_id={job_run.id} "
        f"processed={result['processed']} "
        f"qualified={result['qualified']} "
        f"disqualified={result['disqualified']}"
    )


if __name__ == "__main__":
    main()
