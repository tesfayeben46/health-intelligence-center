#!/usr/bin/env bash
# Restore the eLMIS historical extract from the local gzipped plain pg_dump, and
# provision the read-only `ingestor` role the in-cluster pipeline connects as.
#
# Prereq: `docker compose up -d` and the dump present at
#         ./sample-data/elmis_historical_data_simulated.sql.gz
# Usage:  bash scripts/restore.sh
#
# The dump is streamed (zcat | docker exec -i psql): 1.5 GB is never unpacked to
# disk or copied into the container.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CT=elmis-postgres
DB="${ELMIS_DB_NAME:-elmis}"
DB_USER="${ELMIS_DB_USER:-elmis}"
READER="${ELMIS_READER_USER:-ingestor}"
READER_PW="${ELMIS_READER_PASSWORD:-changeme-elmis-reader}"
DUMP="${ELMIS_DUMP:-sample-data/elmis_historical_data_simulated.sql.gz}"

[ -f "$DUMP" ] || { echo "!! dump not found: $DUMP (see sample-data/SEED-DATA-NOTICE.md)" >&2; exit 1; }

PSQL() { docker exec -i "$CT" psql -v ON_ERROR_STOP=1 -U "$DB_USER" "$@"; }

echo "==> Waiting for $CT to be ready..."
until docker exec "$CT" pg_isready -U "$DB_USER" -d postgres -q 2>/dev/null; do sleep 3; done

echo "==> Verifying archive integrity before touching the database..."
gzip -t "$DUMP" || { echo "!! $DUMP is corrupt: re-transfer it" >&2; exit 1; }

# \restrict / \unrestrict are psql meta-commands added in the Aug-2025 patch
# releases (16.10+). The dump carries them; an older psql aborts on line 5. Detect
# once and strip them from the stream if this client is too old. They are a
# parser guard around the dump body, not data, so removing them is lossless.
STRIP=cat
if docker exec "$CT" psql -U "$DB_USER" -d postgres -qc '\restrict nhdw_probe' >/dev/null 2>&1; then
  docker exec "$CT" psql -U "$DB_USER" -d postgres -qc '\unrestrict nhdw_probe' >/dev/null 2>&1 || true
  echo "    psql understands \\restrict: streaming the dump verbatim"
else
  STRIP='grep -vE ^\\(restrict|unrestrict)'
  echo "    psql predates \\restrict: stripping those two meta-commands from the stream"
fi

echo "==> Dropping & recreating '$DB' (terminating open connections first)..."
PSQL -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null 2>&1 || true
PSQL -d postgres -c "DROP DATABASE IF EXISTS $DB;"
PSQL -d postgres -c "CREATE DATABASE $DB OWNER $DB_USER;"

echo "==> Restoring $DUMP ($(du -h "$DUMP" | cut -f1) gz / ~1.5 GB, 7.6M rows: 5-15 min)..."
# shellcheck disable=SC2086
zcat "$DUMP" | $STRIP | PSQL -d "$DB" -q

echo "==> Provisioning read-only reader '$READER'..."
PSQL -d "$DB" <<PSQLEOF
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$READER') THEN
    CREATE ROLE $READER LOGIN PASSWORD '$READER_PW';
  ELSE
    ALTER ROLE $READER LOGIN PASSWORD '$READER_PW';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE $DB TO $READER;
GRANT USAGE ON SCHEMA reference, history, mutation TO $READER;
GRANT SELECT ON ALL TABLES IN SCHEMA reference, history, mutation TO $READER;
-- Anything a later re-restore adds is readable too, without re-running this.
ALTER DEFAULT PRIVILEGES IN SCHEMA reference, history, mutation GRANT SELECT ON TABLES TO $READER;
PSQLEOF

echo "==> Analyzing (the planner has no stats after a plain-SQL restore)..."
PSQL -d "$DB" -c "ANALYZE;" >/dev/null

echo "==> Row counts:"
PSQL -d "$DB" -c \
"SELECT 'reference.facility'       AS tbl, count(*) FROM reference.facility
 UNION ALL SELECT 'reference.product',        count(*) FROM reference.product
 UNION ALL SELECT 'reference.programme',      count(*) FROM reference.programme
 UNION ALL SELECT 'reference.reporting_period', count(*) FROM reference.reporting_period
 UNION ALL SELECT 'history.requisition',      count(*) FROM history.requisition
 UNION ALL SELECT 'history.requisition_line', count(*) FROM history.requisition_line;"

echo "==> Restore complete. Now: bash scripts/verify.sh"
