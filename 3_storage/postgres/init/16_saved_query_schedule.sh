#!/bin/bash
# Adds scheduling columns to ops_workspace.saved_query so a scheduled runner
# can pick up scheduled rows, run them through /api/exports, and mark them
# as run.
#
# No DAG in 5_orchestrator/dags/ consumes these columns; they are the
# declarative schedule surface. The gateway (/api/queries) stores
# schedule_cron and the dashboard shows it on the Exports page.
#
# Idempotent.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'
ALTER TABLE ops_workspace.saved_query
    ADD COLUMN IF NOT EXISTS schedule_cron          text,
    ADD COLUMN IF NOT EXISTS last_scheduled_run_at  timestamptz,
    ADD COLUMN IF NOT EXISTS last_scheduled_status  text,
    ADD COLUMN IF NOT EXISTS notify_email           text;

CREATE INDEX IF NOT EXISTS idx_saved_query_schedule
    ON ops_workspace.saved_query (schedule_cron)
    WHERE schedule_cron IS NOT NULL;

COMMENT ON COLUMN ops_workspace.saved_query.schedule_cron IS
    'Standard 5-field cron expression (UTC). NULL means run-on-demand only. '
    'Declarative: a scheduled runner (pending; the compose-era '
    'saved_query_runner DAG was archived 2026-07-03) re-runs the export.';
COMMENT ON COLUMN ops_workspace.saved_query.last_scheduled_status IS
    'Result of the most recent scheduled run: ok | error | skipped (e.g. '
    'gateway 5xx). On-demand runs do NOT touch this column.';
SQL
echo "[16_saved_query_schedule] schedule columns ready"
