"""Engine/session helpers for the SQL backend."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from api.core.config import get_settings

Base = declarative_base()


@lru_cache
def get_engine():
    settings = get_settings()
    url = (settings.database_url or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL must be configured to use the SQL backend.")
    return create_engine(url, future=True, pool_pre_ping=True)


@lru_cache
def _get_sessionmaker():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session() -> Session:
    session: Session = _get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
