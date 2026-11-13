#!/bin/bash
# Runs the MDM suites against throwaway Postgres 16 + Trino 476:
#   tests/test_mdm_workspace.py   the workspace read routes (needs Trino)
#   tests/test_mdm_approvals.py   maker-checker: propose -> check -> approve
#
# The ops store gets the real init files (05 ops schemas, 17 permissions,
# 35 MDM, 39 releases, 44 proposals, 45 rules and runs) plus
# tests/fixtures/mdm_ops_candidates.sql
# (synthetic matcher suggestions); the warehouse gets
# tests/fixtures/mdm_workspace_vault.sql (synthetic business-vault objects).
# Trino serves both exactly as in the cluster (catalogs marts + ops), and the
# tests run in python:3.12-slim. Used by CI (test.yml) and runnable locally;
# everything it starts is removed on exit.
#
#   bash 6_access-points/api/nhdw-gateway/tests/run_mdm_tests.sh
#
# Ports (override when busy): PG_PORT (55433), TRINO_PORT (18081).
set -euo pipefail

GW="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$GW/../../.." && pwd)"
PG_PORT="${PG_PORT:-55433}"
TRINO_PORT="${TRINO_PORT:-18081}"
PG_PW="workspace-test"
RUN="mdmws-$$"
CAT="$(mktemp -d)"

cleanup() {
  docker rm -f "$RUN-pg" "$RUN-trino" >/dev/null 2>&1 || true
  rm -rf "$CAT"
}
trap cleanup EXIT

docker run -d --name "$RUN-pg" -p "127.0.0.1:$PG_PORT:5432" -e POSTGRES_PASSWORD="$PG_PW" postgres:16 >/dev/null
for _ in $(seq 1 60); do docker exec "$RUN-pg" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 2
psql_() { docker exec -i "$RUN-pg" psql -U postgres -v ON_ERROR_STOP=1 -q "$@"; }

psql_ -c "CREATE DATABASE nhdw_ops_ci_test" -c "CREATE DATABASE nhdw_vault_ci_test"
psql_ -d nhdw_ops_ci_test < "$REPO/3_storage/postgres/init/05_ops_schemas.sql" >/dev/null
for f in 17_perm_schemas.sql 35_mdm.sql 39_mdm_release.sql 44_mdm_proposal.sql 45_mdm_rules_runs.sql; do
  sed 's/^\\connect nhdw_ops$/\\connect nhdw_ops_ci_test/' "$REPO/3_storage/postgres/init/$f" | psql_ >/dev/null
done
psql_ -d nhdw_ops_ci_test < "$GW/tests/fixtures/mdm_ops_candidates.sql" >/dev/null
psql_ -d nhdw_vault_ci_test < "$GW/tests/fixtures/mdm_workspace_vault.sql" >/dev/null

chmod 755 "$CAT"
printf 'coordinator=true\nnode-scheduler.include-coordinator=true\nhttp-server.http.port=%s\ndiscovery.uri=http://localhost:%s\n' \
  "$TRINO_PORT" "$TRINO_PORT" > "$CAT/config.properties"
mkdir "$CAT/catalog"
for c in marts:nhdw_vault_ci_test ops:nhdw_ops_ci_test; do
  printf 'connector.name=postgresql\nconnection-url=jdbc:postgresql://127.0.0.1:%s/%s\nconnection-user=postgres\nconnection-password=%s\ncase-insensitive-name-matching=true\n' \
    "$PG_PORT" "${c#*:}" "$PG_PW" > "$CAT/catalog/${c%%:*}.properties"
done
chmod -R a+rX "$CAT"
docker run -d --name "$RUN-trino" --network host \
  -v "$CAT/config.properties:/etc/trino/config.properties:ro" -v "$CAT/catalog:/etc/trino/catalog:ro" \
  trinodb/trino:476 >/dev/null
for _ in $(seq 1 90); do
  docker exec "$RUN-trino" trino --server "localhost:$TRINO_PORT" \
    --execute "SELECT 1 FROM marts.nhdw_vault_business.dim_facility LIMIT 1" >/dev/null 2>&1 && break
  sleep 2
done

docker run --rm --network host -v "$GW:/gw:ro" \
  -e OPS_PG_HOST=127.0.0.1 -e OPS_PG_PORT="$PG_PORT" -e OPS_PG_DATABASE=nhdw_ops_ci_test \
  -e OPS_PG_USER=postgres -e OPS_PG_PASSWORD="$PG_PW" \
  -e TRINO_HOST=127.0.0.1 -e TRINO_PORT="$TRINO_PORT" \
  -e TEST_VAULT_DSN="postgresql://postgres:$PG_PW@127.0.0.1:$PG_PORT/nhdw_vault_ci_test" \
  -e TEST_ADMIN_DSN="postgresql://postgres:$PG_PW@127.0.0.1:$PG_PORT/nhdw_ops_ci_test" \
  -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim bash -c \
  "pip install -q --root-user-action=ignore -r /gw/requirements.txt >/dev/null 2>&1 && cd /gw \
   && python -u tests/test_mdm_workspace.py && python -u tests/test_mdm_approvals.py"
