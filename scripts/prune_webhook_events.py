#!/usr/bin/env python3
"""
Reduz payloads brutos antigos de webhooks mantendo metadados auditáveis.

Uso seguro:
  python scripts/prune_webhook_events.py

Executar de verdade:
  python scripts/prune_webhook_events.py --apply

Por padrão, o script apenas simula a limpeza (dry-run). Ele não apaga eventos:
substitui o payload bruto antigo por uma versão mínima, sem dados pessoais
detalhados, preservando provider, event_id, event_type e IDs técnicos úteis.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select

from api.db import models
from api.db.session import get_session


SUCCESS_STATUSES = ("PROCESSED", "IGNORED")
ERROR_STATUSES = ("FAILED", "DEAD_LETTER")


@dataclass(frozen=True)
class PruneResult:
    matched: int
    pruned: int
    dry_run: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_already_pruned(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("_retention"), dict) and payload["_retention"].get("pruned") is True


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compact_payload(event: models.WebhookEvent, *, pruned_at: datetime) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    compact_data = {
        "customer_id": _text(data.get("customer_id")),
        "subscription_id": _text(data.get("subscription_id")),
        "order_id": _text(data.get("order_id")),
        "product_id": _text(data.get("product_id")),
        "plan_id": _text(data.get("plan_id")),
        "native_event": _text(data.get("native_event")),
        "native_object": _text(data.get("native_object")),
    }
    compact_data = {key: value for key, value in compact_data.items() if value}

    return {
        "_retention": {
            "pruned": True,
            "pruned_at": pruned_at.isoformat(),
            "reason": "webhook_payload_retention",
            "original_keys": sorted(str(key) for key in payload.keys()),
        },
        "event_id": event.external_event_id,
        "event_type": event.event_type,
        "provider": event.provider,
        "data": compact_data,
    }


def prune_webhook_payloads(
    *,
    success_payload_days: int = 90,
    error_payload_days: int = 180,
    limit: int = 500,
    dry_run: bool = True,
    now: datetime | None = None,
) -> PruneResult:
    """Prune old webhook raw payloads.

    Successful/ignored events are compacted after ``success_payload_days``.
    Failed/dead-letter events are kept longer and compacted after
    ``error_payload_days``.
    """
    if success_payload_days <= 0:
        raise ValueError("success_payload_days must be greater than zero.")
    if error_payload_days <= 0:
        raise ValueError("error_payload_days must be greater than zero.")
    if limit <= 0:
        raise ValueError("limit must be greater than zero.")

    current = now or _utc_now()
    success_cutoff = current - timedelta(days=success_payload_days)
    error_cutoff = current - timedelta(days=error_payload_days)
    matched = 0
    pruned = 0

    with get_session() as session:
        stmt = (
            select(models.WebhookEvent)
            .where(
                or_(
                    and_(
                        models.WebhookEvent.status.in_(SUCCESS_STATUSES),
                        models.WebhookEvent.received_at < success_cutoff,
                    ),
                    and_(
                        models.WebhookEvent.status.in_(ERROR_STATUSES),
                        models.WebhookEvent.received_at < error_cutoff,
                    ),
                )
            )
            .order_by(models.WebhookEvent.received_at.asc())
            .limit(limit)
        )
        events = session.execute(stmt).scalars().all()
        matched = len(events)

        for event in events:
            if _is_already_pruned(event.payload):
                continue
            pruned += 1
            if not dry_run:
                event.payload = _compact_payload(event, pruned_at=current)

        if not dry_run and pruned:
            session.commit()

    return PruneResult(matched=matched, pruned=pruned, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compacta payloads antigos de webhooks no Postgres.")
    parser.add_argument("--success-days", type=int, default=90, help="Dias para manter payload completo de eventos processados/ignorados.")
    parser.add_argument("--error-days", type=int, default=180, help="Dias para manter payload completo de eventos com erro final.")
    parser.add_argument("--limit", type=int, default=500, help="Quantidade máxima de eventos por execução.")
    parser.add_argument("--apply", action="store_true", help="Executa a limpeza. Sem isso, roda em modo simulação.")
    args = parser.parse_args()

    result = prune_webhook_payloads(
        success_payload_days=args.success_days,
        error_payload_days=args.error_days,
        limit=args.limit,
        dry_run=not args.apply,
    )
    mode = "DRY-RUN" if result.dry_run else "APLICADO"
    print(f"{mode}: eventos encontrados={result.matched}; payloads elegíveis={result.pruned}")
    if result.dry_run:
        print("Nenhuma alteração foi feita. Rode com --apply para executar.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - uso CLI
        sys.stderr.write(f"Erro: {exc}\n")
        raise SystemExit(1)
