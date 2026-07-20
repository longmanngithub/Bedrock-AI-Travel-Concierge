-- Least-privilege runtime role for the backend/worker containers.
--
-- Today `POSTGRES_USER` (the schema owner) is used for both migrations AND
-- runtime queries, so a compromised app process would have full DDL rights
-- (CREATE/DROP/ALTER TABLE) it never actually needs. This script creates a
-- separate `concierge_app` role scoped to exactly what the app does at
-- runtime: SELECT/INSERT/UPDATE/DELETE on rows, nothing else.
--
-- Run this ONCE against the target database, after the schema owner has
-- already run `alembic upgrade head` at least once (the GRANTs below only
-- apply to tables that already exist, plus a default-privileges rule that
-- covers tables created by future migrations automatically).
--
--   psql "$DATABASE_URL" -v app_password="'a-long-random-password'" \
--       -f backend/scripts/create_app_role.sql
--
-- Idempotent: safe to re-run (e.g. after a fresh `alembic upgrade head` adds
-- new tables you want the default-privileges rule to have already covered).

-- \gexec runs the query below, then executes whatever text it returns as SQL
-- — the standard psql idiom for a conditional CREATE ROLE (Postgres has no
-- native `CREATE ROLE IF NOT EXISTS`). No-ops on a second run.
SELECT 'CREATE ROLE concierge_app LOGIN PASSWORD ' || quote_literal(:'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'concierge_app') \gexec

-- No CREATEDB / CREATEROLE / SUPERUSER — this role only ever reads and
-- writes rows in tables the owner role's migrations already created.
ALTER ROLE concierge_app NOCREATEDB NOCREATEROLE NOSUPERUSER;

GRANT USAGE ON SCHEMA public TO concierge_app;

-- Existing tables (run after alembic upgrade head has created them).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO concierge_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO concierge_app;

-- Tables/sequences created by FUTURE migrations (run as the owner role) are
-- automatically covered without re-running this script — as long as the
-- owner role is the one that ran `alembic upgrade head`, matching
-- ALTER DEFAULT PRIVILEGES FOR ROLE below.
ALTER DEFAULT PRIVILEGES FOR ROLE current_user IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO concierge_app;
ALTER DEFAULT PRIVILEGES FOR ROLE current_user IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO concierge_app;
