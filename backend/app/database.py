"""SQLAlchemy engine/session setup for PostgreSQL persistence.

Schema is managed by Alembic (see backend/alembic/). This module no longer
creates or alters tables at runtime — the old `_add_missing_columns()` startup
hack has been removed because it cannot express a NOT NULL column, a foreign
key, an index, or a backfill, all of which the accounts/jobs schema needs.
Instead `check_schema_current()` refuses to serve on a stale schema.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger("app")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def check_schema_current() -> None:
    """Fail loudly at startup if the DB is not migrated to the Alembic head.

    Best-effort: if Alembic isn't importable (e.g. a minimal test image) or the
    version table is missing entirely, we log a warning rather than hard-crash,
    so the failure mode is visible without bricking a fresh dev DB that the
    entrypoint is about to migrate.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except Exception:  # noqa: BLE001 - alembic not installed in this context
        logger.warning("alembic not available; skipping schema check")
        return

    from pathlib import Path

    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    if not ini_path.exists():
        logger.warning("alembic.ini not found at %s; skipping schema check", ini_path)
        return

    head = ScriptDirectory.from_config(Config(str(ini_path))).get_current_head()
    with engine.connect() as conn:
        try:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:  # noqa: BLE001 - version table absent -> unmigrated DB
            raise RuntimeError(
                "Database is not migrated (no alembic_version table). "
                "Run `alembic upgrade head`."
            )
    if current != head:
        raise RuntimeError(
            f"Database schema is stale: at {current!r}, expected head {head!r}. "
            "Run `alembic upgrade head`."
        )


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
