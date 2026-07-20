"""Idempotent, safe database bootstrap run at container start.

Three cases, so a deploy never crashes on the schema:
  * `alembic_version` present            -> just `upgrade head`.
  * legacy DB (trip_records exists, no
    alembic_version)                     -> `stamp 0001` then `upgrade head`.
    This is the existing production DB: 0001 already physically exists there, so
    we must NOT run its CREATE TABLE — we stamp it, then apply 0002+.
  * empty DB                             -> `upgrade head` (runs 0001 onward).

Run:  python -m app.db_bootstrap
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.db_bootstrap")


def _alembic_config() -> Config:
    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    # env.py reads the URL from settings; nothing else to set.
    return cfg


def main() -> None:
    cfg = _alembic_config()
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    has_version = "alembic_version" in tables
    has_legacy = "trip_records" in tables

    if not has_version and has_legacy:
        logger.info("legacy DB detected (trip_records present, no alembic_version) -> stamp 0001")
        command.stamp(cfg, "0001")
    logger.info("running alembic upgrade head")
    command.upgrade(cfg, "head")
    logger.info("database is at head")


if __name__ == "__main__":
    main()
