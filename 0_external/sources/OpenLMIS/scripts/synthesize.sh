#!/usr/bin/env bash
# Synthesize the Zambian eLMIS historical star into the running elmis-postgres.
#
# Fallback when the separately distributed historical dump is absent: generates
# a synthetic Zambian supply history from the committed entity model and
# facility registry (deterministic, seeded). Same contract as restore.sh: fills
# reference.* and history.*, then provisions the read-only `ingestor` the
# pipeline connects as. The ingestor, vault and marts run unchanged on top.
set -euo pipefail
here() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }
SRC="$(here)"                       # 0_external/sources/OpenLMIS
REPO="$(cd "$SRC/../../.." && pwd)"

CT="${ELMIS_CT:-elmis-postgres}"
DB="${ELMIS_DB_NAME:-elmis}"
DB_USER="${ELMIS_DB_USER:-elmis}"
READER="${ELMIS_READER_USER:-ingestor}"
READER_PW="${ELMIS_READER_PASSWORD:-changeme-elmis-reader}"
ENTITY_MODEL="${ENTITY_MODEL:-$REPO/bundle/seeds/entity-model/entity-model.zm.yaml}"
FACILITIES="${FACILITIES:-$REPO/bundle/seeds/entity-model/sample-data/facilities_zm_elmis.json}"
SCHEMA="$SRC/schema-only.sql"
GEN="$SRC/scripts/synthesize_zm_history.py"
DATA="$(mktemp /tmp/elmis_zm_data.XXXXXX.sql)"
trap 'rm -f "$DATA"' EXIT

PSQL() { docker exec -i "$CT" psql -v ON_ERROR_STOP=1 -U "$DB_USER" "$@"; }

echo "==> Waiting for $CT..."
until docker exec "$CT" pg_isready -U "$DB_USER" -d postgres -q 2>/dev/null; do sleep 3; done

echo "==> Generating Zambian supply history (deterministic; ~40s)..."
command -v python3 >/dev/null || { echo "!! python3 required" >&2; exit 1; }
python3 -c 'import yaml' 2>/dev/null || pip install --quiet --user pyyaml 2>/dev/null || \
  pip install --quiet --break-system-packages pyyaml 2>/dev/null || true
python3 "$GEN" --entity-model "$ENTITY_MODEL" --facilities "$FACILITIES" --out "$DATA"

echo "==> Recreating '$DB' and applying the eLMIS schema..."
PSQL -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null 2>&1 || true
PSQL -d postgres -c "DROP DATABASE IF EXISTS $DB;"
PSQL -d postgres -c "CREATE DATABASE $DB OWNER $DB_USER;"
# schema-only.sql grants to a NOLOGIN role `elmis_reader`; it must pre-exist.
PSQL -d postgres -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='elmis_reader') THEN CREATE ROLE elmis_reader NOLOGIN; END IF; END \$\$;"
docker exec -i "$CT" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB" -q < "$SCHEMA"

echo "==> Loading synthesized data (FK-validated; USER triggers off for the load)..."
docker exec -i "$CT" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB" -q < "$DATA"

echo "==> Provisioning read-only reader '$READER'..."
PSQL -d "$DB" >/dev/null <<PSQLEOF
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
ALTER DEFAULT PRIVILEGES IN SCHEMA reference, history, mutation GRANT SELECT ON TABLES TO $READER;
PSQLEOF

echo "==> Analyzing..."
PSQL -d "$DB" -c "ANALYZE;" >/dev/null
echo "==> eLMIS Zambian history synthesized:"
PSQL -d "$DB" -At -c "SELECT 'facilities='||count(*) FROM reference.facility;
SELECT 'requisitions='||count(*) FROM history.requisition;
SELECT 'requisition_lines='||count(*) FROM history.requisition_line;" | sed 's/^/    /'
