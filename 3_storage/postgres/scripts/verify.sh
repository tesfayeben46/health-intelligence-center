#!/bin/bash
# Smoke test of the Postgres vault engine in a local container named
# postgres-vault:
#
#   - admin can connect
#   - exactly three nhdw_* schemas exist
#   - landing_writer can create a table in nhdw_landing
#   - vault_writer can SELECT from it
#   - bi_reader is denied on landing
set -eu

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (cp .env.example .env)" >&2; exit 1; }
. ./.env

run() {
    docker exec -e PGPASSWORD="$2" postgres-vault \
        psql -h localhost -U "$1" -d "$POSTGRES_DB" -tA -c "$3" 2>&1
}

ok() { printf "  \033[32mok\033[0m   %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m %s\n       %s\n" "$1" "$2"; exit 1; }

echo "== Admin"
v=$(run "$POSTGRES_ADMIN_USER" "$POSTGRES_ADMIN_PASSWORD" "SELECT current_user")
[ "$v" = "$POSTGRES_ADMIN_USER" ] && ok "admin can connect" || fail "admin connect" "$v"

echo "== Schemas"
v=$(run "$POSTGRES_ADMIN_USER" "$POSTGRES_ADMIN_PASSWORD" "SELECT count(*) FROM information_schema.schemata WHERE schema_name LIKE 'nhdw_%'")
[ "$v" = "3" ] && ok "nhdw_landing + nhdw_vault_raw + nhdw_vault_business present" || fail "schemas" "found $v"

echo "== landing_writer DDL on nhdw_landing"
run landing_writer "$LANDING_WRITER_PASSWORD" "CREATE TABLE IF NOT EXISTS nhdw_landing._verify (i int)" > /dev/null
run landing_writer "$LANDING_WRITER_PASSWORD" "INSERT INTO nhdw_landing._verify VALUES (1)" > /dev/null
v=$(run landing_writer "$LANDING_WRITER_PASSWORD" "SELECT count(*) FROM nhdw_landing._verify")
[ "$v" -ge 1 ] && ok "landing_writer create + insert OK" || fail "landing_writer" "$v"

echo "== vault_writer SELECT on nhdw_landing"
v=$(run vault_writer "$VAULT_WRITER_PASSWORD" "SELECT count(*) FROM nhdw_landing._verify")
[ "$v" -ge 1 ] && ok "vault_writer can read landing" || fail "vault_writer" "$v"

echo "== vault_writer DDL on nhdw_vault_raw"
run vault_writer "$VAULT_WRITER_PASSWORD" "CREATE TABLE IF NOT EXISTS nhdw_vault_raw._verify (i int)" > /dev/null
ok "vault_writer can create in vault_raw"

echo "== bi_reader scoped"
# psql exits 1 on permission denied; capture it without aborting the script.
v=$(run bi_reader "$BI_READER_PASSWORD" "SELECT count(*) FROM nhdw_landing._verify" || true)
if echo "$v" | grep -q "permission denied"; then
    ok "bi_reader correctly blocked from landing"
else
    fail "bi_reader" "should have been denied: $v"
fi

echo "== Cleanup"
run "$POSTGRES_ADMIN_USER" "$POSTGRES_ADMIN_PASSWORD" "DROP TABLE IF EXISTS nhdw_landing._verify, nhdw_vault_raw._verify" > /dev/null
ok "verify tables dropped"

echo
echo "== All checks passed."
