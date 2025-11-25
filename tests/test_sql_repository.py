"""
Smoke tests for the SQLRepository against a temporary SQLite database.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Garante que o pacote api seja importável durante os testes locais
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.db import session as db_session
from api.db import models
from api.repositories.sql_repository import SQLRepository
from api.core import config as core_config


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Configura um SQLite temporário e garante teardown completo para não deixar o arquivo bloqueado no Windows."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    # limpa caches para forçar re-leitura de envs
    core_config.get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session._get_sessionmaker.cache_clear()  # type: ignore[attr-defined]

    engine = db_session.get_engine()
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)

    yield db_file

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


def test_card_and_slug_flow(temp_db):
    repo = SQLRepository()
    repo.upsert_user("alice@example.com", password_hash="hash")
    repo.create_card("uid123", "111111", vanity=None, owner_email=None)
    assert not repo.slug_exists("alice")
    repo.update_card_slug("uid123", "alice")
    assert repo.slug_exists("alice")
    repo.assign_card_owner("uid123", "alice@example.com", vanity="alice")
    card = repo.get_card_by_uid("uid123")
    assert card is not None
    assert card.owner_email == "alice@example.com"
    assert card.vanity == "alice"


def test_domain_registry(temp_db):
    repo = SQLRepository()
    repo.create_card("uid999", "222222")
    repo.register_custom_domain("example.test", "uid999")
    card = repo.get_card_by_custom_domain("example.test")
    assert card is not None
    assert card.uid == "uid999"
    repo.unregister_custom_domain("example.test")
    assert repo.get_card_by_custom_domain("example.test") is None


def test_admin_session(temp_db):
    repo = SQLRepository()
    tok = repo.create_admin_session("admin@example.com", csrf_token="csrf123", expires_at=models.func.now())  # type: ignore[attr-defined]
    sess = repo.get_admin_session(tok)
    assert sess is not None
    assert sess.csrf_token == "csrf123"
    repo.delete_admin_session(tok)
    assert repo.get_admin_session(tok) is None
