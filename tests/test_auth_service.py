from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

# Garante que o pacote api seja importável durante os testes locais
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.services.auth_service import AuthService


def test_token_expired_respects_timezone_offset():
    svc = AuthService()
    now = svc._now()
    created = datetime.now(timezone(timedelta(hours=-3)))

    assert svc._token_expired(created, now, ttl_seconds=900) is False

    old = created - timedelta(hours=1)
    assert svc._token_expired(old, now, ttl_seconds=900) is True
