"""SQLAlchemy engine/session setup for PostgreSQL persistence."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_missing_columns() -> None:
    """Add any ORM-declared columns absent from an already-existing table.

    `Base.metadata.create_all` only creates tables that don't exist yet — it
    never alters an existing table, so a column added to a model (e.g.
    `client_id`, `model`, `generation_config`) would silently never appear on
    a dev DB created before that change, and every query would then fail on
    the missing column. There's no Alembic/migration framework in this
    project (a deliberate scope choice for a course project), so this is a
    minimal, idempotent stand-in: for each declared table that already
    exists, diff its columns against the DB and `ADD COLUMN IF NOT EXISTS`
    for anything missing. Safe to run on every startup.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # create_all will create it fresh with every column
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = column.type.compile(dialect=conn.dialect)
                conn.execute(
                    text(
                        f'ALTER TABLE "{table.name}" '
                        f'ADD COLUMN IF NOT EXISTS "{column.name}" {ddl}'
                    )
                )


def init_db() -> None:
    """Create tables if they do not exist (called on FastAPI startup)."""
    from . import models  # noqa: F401  (register models before create_all)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
