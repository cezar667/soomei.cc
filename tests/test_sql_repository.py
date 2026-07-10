"""
Smoke tests for the SQLRepository against a temporary SQLite database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.core import config as core_config
from api.db import models
from api.db import session as db_session
from api.repositories.sql_repository import SQLRepository


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
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


def test_search_cards_filters_and_paginates(temp_db):
    repo = SQLRepository()
    repo.upsert_user("owner@example.com", password_hash="hash")
    for idx in range(5):
        repo.create_card(f"uid{idx}", f"11111{idx}", vanity=f"slug-{idx}", owner_email="owner@example.com")
    repo.update_card_status("uid1", "blocked")
    repo.update_card_status("uid2", "blocked")

    page_one = repo.search_cards(status="blocked", page=1, page_size=1)
    page_two = repo.search_cards(status="blocked", page=2, page_size=1)
    by_query = repo.search_cards(q="slug-4", page=1, page_size=10)

    assert page_one.total == 2
    assert page_one.pages == 2
    assert len(page_one.items) == 1
    assert len(page_two.items) == 1
    assert {card.uid for card in page_one.items + page_two.items} == {"uid1", "uid2"}
    assert [card.uid for card in by_query.items] == ["uid4"]


def test_search_users_filters_and_paginates(temp_db):
    repo = SQLRepository()
    for idx in range(4):
        repo.upsert_user(f"user{idx}@example.com", password_hash="hash")

    first_page = repo.search_users(q="user", page=1, page_size=2)
    second_page = repo.search_users(q="user", page=2, page_size=2)
    filtered = repo.search_users(q="user3", page=1, page_size=10)

    assert first_page.total == 4
    assert first_page.pages == 2
    assert len(first_page.items) == 2
    assert len(second_page.items) == 2
    assert [user.email for user in filtered.items] == ["user3@example.com"]


def test_domain_registry_and_conflict_queries(temp_db):
    repo = SQLRepository()
    repo.create_card("uid999", "222222", vanity="demo-card")
    repo.create_card("uid888", "333333", vanity="pending-card")

    repo.register_custom_domain("example.test", "uid999")
    repo.update_card_custom_domain_meta("uid999", {"active_host": "example.test", "status": "active"})
    repo.update_card_custom_domain_meta("uid888", {"requested_host": "pending.test", "status": "pending"})

    by_registry = repo.get_card_by_custom_domain("example.test")
    by_json = repo.get_card_by_custom_domain("EXAMPLE.TEST")

    assert by_registry is not None
    assert by_registry.uid == "uid999"
    assert by_json is not None
    assert by_json.uid == "uid999"
    assert repo.custom_domain_conflict_exists("example.test") is True
    assert repo.custom_domain_conflict_exists("pending.test") is True
    assert repo.custom_domain_conflict_exists("pending.test", exclude_uid="uid888") is False

    domains_page = repo.list_cards_with_custom_domains(page=1, page_size=10)
    assert domains_page.total == 2
    assert {card.uid for card in domains_page.items} == {"uid999", "uid888"}

    repo.unregister_custom_domain("example.test")
    assert repo.get_card_by_custom_domain("example.test") is not None


def test_dashboard_aggregations(temp_db):
    repo = SQLRepository()
    repo.create_card("uidA", "123456", vanity="card-a")
    repo.create_card("uidB", "123457", vanity="card-b")
    repo.create_card("uidC", "123458", vanity="card-c")
    repo.update_card_status("uidA", "active")
    repo.update_card_status("uidB", "blocked")
    repo.increment_card_views("uidA")
    repo.increment_card_views("uidA")
    repo.increment_card_views("uidB")

    counts = repo.dashboard_card_counts()
    top = repo.top_cards_by_views(limit=2)

    assert counts["total"] == 3
    assert counts["active"] == 1
    assert counts["blocked"] == 1
    assert counts["pending"] == 1
    assert top[0] == ("card-a", 2)
    assert top[1] == ("card-b", 1)


def test_admin_session(temp_db):
    repo = SQLRepository()
    token = repo.create_admin_session("admin@example.com", csrf_token="csrf123", expires_at=models.func.now())  # type: ignore[arg-type]
    session = repo.get_admin_session(token)
    assert session is not None
    assert session.csrf_token == "csrf123"
    repo.delete_admin_session(token)
    assert repo.get_admin_session(token) is None
