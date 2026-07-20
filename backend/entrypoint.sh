#!/bin/sh
# Container entrypoint. Only the API container migrates the DB (ALEMBIC_SKIP
# unset); the worker container sets ALEMBIC_SKIP=1 so the two don't race the
# same migration on `docker compose up`.
set -e

if [ "${ALEMBIC_SKIP:-0}" != "1" ]; then
    echo "[entrypoint] bootstrapping database schema..."
    python -m app.db_bootstrap
fi

# The API container's crew-free process is cheap enough to run more than one
# uvicorn worker (see main.py's docstring on why torch never lands here). The
# worker container's command is `arq ...`, not uvicorn, so it's untouched.
if [ "${1:-}" = "uvicorn" ]; then
    exec "$@" --workers "${WEB_CONCURRENCY:-2}"
else
    exec "$@"
fi
