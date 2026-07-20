"""Background job queue (arq + Redis).

Redis holds live progress only; Postgres is the source of truth for job rows
and results. See app/queue/worker.py for the worker entrypoint.
"""
