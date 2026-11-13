#!/usr/bin/env bash
# Verify the eLMIS replica is reachable on the same interface the real system
# would expose to an ingestor: Postgres over the wire, as the read-only reader.
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
PORT="${ELMIS_DB_PORT:-5434}"

fail=0

echo "== 1/4 Postgres reachability (reader role, over TCP) =="
if docker exec -e PGPASSWORD="$READER_PW" -i "$CT" \
     psql -h 127.0.0.1 -U "$READER" -d "$DB" -Atc "SELECT 'pg up';" 2>/dev/null | grep -q "pg up"
then
  echo "   PASS: $READER can connect to $DB"
else
  echo "   FAIL: Postgres down or reader '$READER' not provisioned (run scripts/restore.sh)"; fail=1
fi

echo ""
echo "== 2/4 Published port $PORT (what cluster pods reach via host.k3d.internal) =="
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  echo "   PASS: 0.0.0.0:$PORT accepting connections"
else
  echo "   FAIL: nothing listening on $PORT"; fail=1
fi

echo ""
echo "== 3/4 Row counts =="
docker exec -i "$CT" psql -U "$DB_USER" -d "$DB" -c "
SELECT 'reference.facility'         AS t, COUNT(*) AS n FROM reference.facility
UNION ALL SELECT 'reference.product',            COUNT(*) FROM reference.product
UNION ALL SELECT 'reference.programme',          COUNT(*) FROM reference.programme
UNION ALL SELECT 'reference.geographic_zone',    COUNT(*) FROM reference.geographic_zone
UNION ALL SELECT 'reference.reporting_period',   COUNT(*) FROM reference.reporting_period
UNION ALL SELECT 'history.requisition',          COUNT(*) FROM history.requisition
UNION ALL SELECT 'history.requisition_line',     COUNT(*) FROM history.requisition_line;
" 2>/dev/null || { echo "   FAIL: could not read row counts"; fail=1; }

echo ""
echo "== 4/4 Referential integrity + coverage (the ingestor's assumptions) =="
docker exec -i "$CT" psql -U "$DB_USER" -d "$DB" -Atc "
SELECT 'orphan requisition_line: ' || COUNT(*) FROM history.requisition_line l
  LEFT JOIN history.requisition r USING (source_requisition_id) WHERE r.source_requisition_id IS NULL;
" 2>/dev/null
docker exec -i "$CT" psql -U "$DB_USER" -d "$DB" -Atc "
SELECT 'reporting period span: ' || MIN(period_start)::date || ' .. ' || MAX(period_end)::date
  FROM reference.reporting_period;
" 2>/dev/null
docker exec -i "$CT" psql -U "$DB_USER" -d "$DB" -Atc "
SELECT 'installed mutation scenario: ' || COALESCE(string_agg(scenario_code || ' (' || state || ')', ', '), 'none')
  FROM mutation.scenario_manifest;
" 2>/dev/null

echo ""
[ "$fail" = 0 ] && echo "eLMIS verify: PASS" || { echo "eLMIS verify: FAIL"; exit 1; }
