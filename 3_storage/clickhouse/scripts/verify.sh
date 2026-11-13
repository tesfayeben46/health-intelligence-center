#!/bin/bash
# Smoke test of a local ClickHouse container (named clickhouse) and its
# object-store (s3()) connectivity.
#
# Exit 0  : engine healthy, named collection works, raw views read parquet
# Exit 1  : something is wrong (per-check error line printed)
#
# Run: bash scripts/verify.sh

set -eu

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (cp .env.example .env)" >&2; exit 1; }
. ./.env

CC="docker exec clickhouse clickhouse-client --user staging_writer --password $CLICKHOUSE_STAGING_PASSWORD"

ok() { printf "  \033[32mok\033[0m   %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m %s\n       %s\n" "$1" "$2"; exit 1; }

echo "== Engine ping"
docker exec clickhouse wget -qO- http://localhost:8123/ping | grep -q Ok \
    && ok "HTTP /ping returns Ok" || fail "/ping" "no response"

echo "== Databases"
# Uses `default` (admin): staging_writer has no grants on nhdw_marts and
# cannot see it in system.databases.
dbs=$(docker exec clickhouse clickhouse-client --query "SELECT name FROM system.databases WHERE name LIKE 'nhdw%' ORDER BY 1")
[ "$(echo "$dbs" | wc -l)" -eq 2 ] && ok "nhdw_staging + nhdw_marts present" \
    || fail "databases" "missing one of nhdw_staging/nhdw_marts: $dbs"

echo "== object-store named collection (staging_writer, nhdw_minio on SeaweedFS)"
$CC --query "SELECT count() FROM nhdw_staging.raw_dhis2__organisation_units" > /tmp/cc_test 2>&1 \
    && ok "s3() read OK ($(cat /tmp/cc_test) rows in dhis2.organisation_units)" \
    || fail "s3() read" "$(cat /tmp/cc_test)"

echo "== Per-source row counts"
for v in raw_dhis2__data_value_sets \
         raw_dhis2__organisation_units \
         raw_dhis2__data_elements \
         raw_ethioemr__patient \
         raw_hcmiswdm__fact_dailyrecord \
         raw_mbrana__mflowussd_sohlogs; do
    n=$($CC --query "SELECT count() FROM nhdw_staging.$v" 2>/dev/null || echo "ERR")
    printf "    %-40s %s\n" "$v" "$n"
done

echo
echo "== All checks passed."
