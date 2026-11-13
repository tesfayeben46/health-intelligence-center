#!/usr/bin/env bash
# Restore the OpenMRS dump into the running MySQL container.
# Idempotent: drops + recreates the openmrs DB before importing.
#
# Prereq: containers running (`docker compose up -d mysql`) and healthy
# Usage:  bash scripts/restore.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# Load .env so we have ETHIOEMR_MYSQL_ROOT_PASSWORD etc.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "ERROR: .env not found. Run: cp .env.example .env" >&2
  exit 1
fi

CONTAINER=ethioemr-mysql
DUMP_SRC="sample-data/clean_openmrs.sql"
DUMP_IN_CONTAINER="/tmp/dump.sql"
DB="${ETHIOEMR_DB_NAME:-openmrs}"
ROOT_PASS="${ETHIOEMR_MYSQL_ROOT_PASSWORD:?ETHIOEMR_MYSQL_ROOT_PASSWORD must be set}"
READER_PASS="${ETHIOEMR_READER_PASSWORD:?ETHIOEMR_READER_PASSWORD must be set}"
OMRS_PASS="${OPENMRS_DB_PASSWORD:?OPENMRS_DB_PASSWORD must be set}"

# Confirm the dump (or the file a symlink points to) exists.
if [ ! -e "$DUMP_SRC" ]; then
  echo "ERROR: dump missing at $DUMP_SRC (symlink target may be unavailable)" >&2
  exit 1
fi

echo "==> Waiting for $CONTAINER to be healthy..."
for _ in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "starting")
  if [ "$status" = "healthy" ]; then
    break
  fi
  sleep 2
done
if [ "$status" != "healthy" ]; then
  echo "ERROR: $CONTAINER did not reach healthy status. Logs:" >&2
  docker logs --tail 30 "$CONTAINER" >&2
  exit 1
fi

echo "==> Copying dump into container..."
# Resolve a symlink so docker cp copies the file.
docker cp "$(readlink -f "$DUMP_SRC")" "$CONTAINER:$DUMP_IN_CONTAINER"

echo "==> Dropping and recreating database '$DB'..."
docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PASS" -e "
DROP DATABASE IF EXISTS \`$DB\`;
CREATE DATABASE \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
"

echo "==> Restoring dump (147 MB: takes 3-8 minutes)..."
docker exec -i "$CONTAINER" bash -c "mysql -uroot -p\"$ROOT_PASS\" \"$DB\" < \"$DUMP_IN_CONTAINER\""

echo "==> Removing dump from container..."
docker exec -i "$CONTAINER" rm -f "$DUMP_IN_CONTAINER"

echo "==> Provisioning service users..."
docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PASS" <<SQL
-- Read-only ingestor user (CDC / direct extract).
CREATE USER IF NOT EXISTS 'ingestor'@'%' IDENTIFIED BY '${READER_PASS}';
ALTER USER 'ingestor'@'%' IDENTIFIED BY '${READER_PASS}';
GRANT SELECT ON \`${DB}\`.* TO 'ingestor'@'%';
GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'ingestor'@'%';

-- OpenMRS web app's DB user.
CREATE USER IF NOT EXISTS 'openmrs'@'%' IDENTIFIED BY '${OMRS_PASS}';
ALTER USER 'openmrs'@'%' IDENTIFIED BY '${OMRS_PASS}';
GRANT ALL PRIVILEGES ON \`${DB}\`.* TO 'openmrs'@'%';

FLUSH PRIVILEGES;
SQL

echo "==> Verifying row counts..."
docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PASS" "$DB" -e "
SELECT 'patient'        AS table_name, COUNT(*) AS row_count FROM patient
UNION ALL SELECT 'person',          COUNT(*) FROM person
UNION ALL SELECT 'encounter',       COUNT(*) FROM encounter
UNION ALL SELECT 'obs',             COUNT(*) FROM obs
UNION ALL SELECT 'visit',           COUNT(*) FROM visit
UNION ALL SELECT 'concept',         COUNT(*) FROM concept
UNION ALL SELECT 'concept_name',    COUNT(*) FROM concept_name
UNION ALL SELECT 'location',        COUNT(*) FROM location
UNION ALL SELECT 'provider',        COUNT(*) FROM provider;
"

echo ""
echo "OK: restore complete."
echo "Next: start the OpenMRS web container:"
echo "  docker compose up -d openmrs"
echo "Then wait ~2 min and run: bash scripts/verify.sh"
