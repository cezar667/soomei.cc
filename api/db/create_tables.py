"""Utility script to create the initial database schema."""
from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from .session import Base, get_engine
from . import models  # noqa: F401  # ensure models are imported for metadata


def create_all() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    try:
        create_all()
        print("Database tables created successfully.")
    except SQLAlchemyError as exc:
        raise SystemExit(f"Failed to create tables: {exc}") from exc
