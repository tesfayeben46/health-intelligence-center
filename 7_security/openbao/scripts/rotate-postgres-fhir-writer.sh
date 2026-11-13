#!/bin/bash
# Static-secret rotation for the postgres-vault fhir_writer role.
#
# Postgres can issue dynamic creds via OpenBao's database engine, but
# HAPI's JPA pool is happiest with a single stable identity per boot. We
# keep fhir_writer a static role and rotate via this script (same pattern
# as 7_security/openbao/scripts/rotate-clickhouse-staging-writer.sh).
#
# Run it:
#   - on initial setup (writes the random password into OpenBao)
#   - periodically (ad hoc or from cron)
#   - after the HAPI container restarts and re-reads the cred via .env
#
# Requires OPENBAO_ADDR and OPENBAO_TOKEN (root or a policy with kv put on
# the path) plus vault_admin Postgres access via the host's psql client.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
OPENBAO_ADDR="${OPENBAO_ADDR:-http://127.0.0.1:8200}"
: "${OPENBAO_TOKEN:?OPENBAO_TOKEN required}"

USERNAME=fhir_writer
NEW_PW=$(openssl rand -hex 24)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

ADMIN_PW=$(grep ^POSTGRES_ADMIN_PASSWORD "$REPO/3_storage/postgres/.env" | cut -d= -f2)

echo "rotating Postgres role '$USERNAME' at $NOW"

docker exec -e PGPASSWORD="$ADMIN_PW" postgres-vault psql -U vault_admin -d nhdw_vault \
    -c "ALTER ROLE \"$USERNAME\" WITH LOGIN PASSWORD '$NEW_PW'" >/dev/null

HTTP=$(curl -sS -o /tmp/.bao_rot_resp -w '%{http_code}' \
    -X POST \
    -H "X-Vault-Token: $OPENBAO_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"data\":{\"username\":\"$USERNAME\",\"password\":\"$NEW_PW\",\"rotated_at\":\"$NOW\"}}" \
    "$OPENBAO_ADDR/v1/secret/data/postgres/fhir-writer")
case "$HTTP" in
    200|204) echo "wrote new password to OpenBao kv (HTTP $HTTP)" ;;
    *) echo "kv write failed HTTP $HTTP; $(cat /tmp/.bao_rot_resp)" >&2; exit 1 ;;
esac
rm -f /tmp/.bao_rot_resp

# Smoke: connect as fhir_writer with the new pw.
docker exec -e PGPASSWORD="$NEW_PW" postgres-vault psql -U "$USERNAME" -d nhdw_vault \
    -c "SELECT current_user, '$NOW' AS rotated_at" \
    || { echo "post-rotate smoke FAILED" >&2; exit 1; }

echo "rotation complete"
