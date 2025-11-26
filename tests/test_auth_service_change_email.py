from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Garante que o pacote api seja importável durante os testes locais
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.db import models  # noqa: E402
from api.db import session as db_session  # noqa: E402
from api.core import config as core_config  # noqa: E402
import api.services.auth_service as auth_service  # noqa: E402
from api.repositories.sql_repository import SQLRepository  # noqa: E402
from api.services.auth_service import AuthService  # noqa: E402


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """Configura um SQLite temporário e reseta caches de settings/engine."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    core_config.get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session._get_sessionmaker.cache_clear()  # type: ignore[attr-defined]

    engine = db_session.get_engine()
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)

    yield

    try:
        models.Base.metadata.drop_all(bind=engine)
    except Exception:
        pass
    try:
        engine.dispose()
    except Exception:
        pass
    db_session.get_engine.cache_clear()
    db_session._get_sessionmaker.cache_clear()  # type: ignore[attr-defined]
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass


def test_change_pending_email_moves_owner_and_returns_new_verify_path(db_env, monkeypatch):
    repo = SQLRepository()
    svc = AuthService()
    monkeypatch.setattr(auth_service, "send_email", lambda *a, **kw: True)

    repo.create_card("uid123", "111111", vanity="old-slug", owner_email="old@example.com")
    repo.assign_card_owner("uid123", "old@example.com", status="active", vanity="old-slug")
    repo.upsert_user("old@example.com", password_hash="hash")
    repo.create_verify_token("old@example.com", token="tok-old")

    new_email, verify_path, reason = svc.change_pending_email("uid123", "111111", "new@example.com")

    assert reason is None
    assert new_email == "new@example.com"
    assert verify_path.startswith("/auth/verify?token=")

    card = repo.get_card_by_uid("uid123")
    assert card.owner_email == "new@example.com"
    assert (card.status or "").lower() == "active"
    assert repo.get_user("new@example.com") is not None
    assert repo.get_user("old@example.com") is None

    token_entity = repo.get_verify_token_for_email("new@example.com")
    assert token_entity is not None
    assert token_entity.token in verify_path
    assert repo.get_verify_token("tok-old") is None  # tokens antigos foram limpos


def test_change_pending_email_requires_valid_pin(db_env, monkeypatch):
    repo = SQLRepository()
    svc = AuthService()
    monkeypatch.setattr(auth_service, "send_email", lambda *a, **kw: True)

    repo.create_card("uid999", "222222", vanity=None, owner_email="owner@test.com")
    repo.assign_card_owner("uid999", "owner@test.com", status="active", vanity=None)
    repo.upsert_user("owner@test.com", password_hash="hash")

    new_email, verify_path, reason = svc.change_pending_email("uid999", "999999", "new@test.com")

    assert new_email is None
    assert verify_path is None
    assert reason == "invalid_pin"

    card = repo.get_card_by_uid("uid999")
    assert card.owner_email == "owner@test.com"
