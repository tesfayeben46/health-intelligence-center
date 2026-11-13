#!/usr/bin/env bash
# Restore the seeded DHIS2 database from a local custom-format dump instead of
# seeding through the API.
#
# Prereq: `docker compose up -d dhis2-db` and the dump present at
#         ./sample-data/dhis2_seeded.pgdump (written by bundle/scripts/pack.sh)
# Usage:  bash scripts/restore.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DB=dhis2
DB_USER=dhis
DUMP=/tmp/dhis2_seeded.pgdump
# Copy the dump into the container first.
docker cp sample-data/dhis2_seeded.pgdump dhis2-db:$DUMP

echo "==> Waiting for dhis2_db to be ready..."
until docker exec dhis2-db pg_isready -U "$DB_USER" -d postgres -q; do sleep 3; done

echo "==> Dropping & recreating '$DB' (terminating open connections first)..."
docker exec dhis2-db psql -U "$DB_USER" -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null 2>&1 || true
docker exec dhis2-db psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB;"
docker exec dhis2-db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB OWNER $DB_USER;"

echo "==> Restoring from $DUMP (parallel, 5-20 min)..."
docker exec dhis2-db pg_restore -U "$DB_USER" -d "$DB" --no-owner --role="$DB_USER" -j 4 "$DUMP"

echo "==> Row counts:"
docker exec dhis2-db psql -U "$DB_USER" -d "$DB" -c \
"SELECT 'organisationunit' AS tbl, count(*) FROM organisationunit
 UNION ALL SELECT 'dataelement', count(*) FROM dataelement
 UNION ALL SELECT 'datavalue',   count(*) FROM datavalue;"

echo "==> Restore complete. Now: docker compose up -d dhis2"
