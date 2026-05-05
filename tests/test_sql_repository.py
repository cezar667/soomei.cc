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


def test_search_cards_uses_sql_filters_and_pagination(temp_db):
    repo = SQLRepository()
    repo.upsert_user("alice@example.com", password_hash="hash")
    repo.upsert_user("bob@example.com", password_hash="hash")
    repo.create_card("uid001", "111111", vanity="alice-card", owner_email="alice@example.com")
    repo.assign_card_owner("uid001", "alice@example.com", status="active", vanity="alice-card")
    repo.create_card("uid002", "222222", vanity="blocked-card", owner_email="bob@example.com")
    repo.assign_card_owner("uid002", "bob@example.com", status="blocked", vanity="blocked-card")
    repo.create_card("uid003", "333333", vanity="pending-card", owner_email=None)

    result = repo.search_cards(q="alice", status="active", page=1, page_size=10)

    assert result.total == 1
    assert [c.uid for c in result.items] == ["uid001"]

    paged = repo.search_cards(page=2, page_size=2)
    assert paged.total == 3
    assert paged.page == 2
    assert len(paged.items) == 1


def test_search_users_uses_sql_filters_and_pagination(temp_db):
    repo = SQLRepository()
    repo.upsert_user("alice@example.com", password_hash="hash")
    repo.upsert_user("bob@example.com", password_hash="hash")
    repo.upsert_user("carol@example.com", password_hash="hash")

    result = repo.search_users(q="bob", page=1, page_size=10)

    assert result.total == 1
    assert [u.email for u in result.items] == ["bob@example.com"]

    paged = repo.search_users(page=2, page_size=2)
    assert paged.total == 3
    assert paged.page == 2
    assert len(paged.items) == 1


def test_domain_queries_avoid_full_scan_helpers(temp_db):
    repo = SQLRepository()
    repo.create_card("uid-active", "111111", vanity="card-active")
    repo.create_card("uid-pending", "222222", vanity="card-pending")
    repo.create_card("uid-other", "333333", vanity="card-other")

    repo.update_card_custom_domain_meta(
        "uid-active",
        {"active_host": "active.example.test", "status": "active", "admin_note": "ok"},
    )
    repo.update_card_custom_domain_meta(
        "uid-pending",
        {"requested_host": "pending.example.test", "status": "pending"},
    )

    active = repo.get_card_by_custom_domain("active.example.test")
    assert active is not None
    assert active.uid == "uid-active"

    assert repo.custom_domain_conflict_exists("active.example.test") is True
    assert repo.custom_domain_conflict_exists("pending.example.test") is True
    assert repo.custom_domain_conflict_exists("pending.example.test", exclude_uid="uid-pending") is False

    page = repo.list_cards_with_custom_domains(page=1, page_size=10)
    assert page.total == 2
    assert {card.uid for card in page.items} == {"uid-active", "uid-pending"}
