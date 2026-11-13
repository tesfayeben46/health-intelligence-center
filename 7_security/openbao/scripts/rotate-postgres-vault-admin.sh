#!/bin/bash
# Static-secret rotation for the postgres-vault `vault_admin` superuser.
#
# Same pattern as rotate-postgres-fhir-writer.sh and
# rotate-clickhouse-staging-writer.sh. Moves the vault_admin password out of
# the plaintext 3_storage/postgres/.env into OpenBao.
#
# vault_admin is the superuser that:
#   - the staging / vault / marts pipelines connect as for DDL
#   - OpenBao itself uses as the DB engine's root credential
#
# Because it is an admin role with a broad blast radius, this rotation:
#   1. issues a new random password
#   2. ALTERs the Postgres role
#   3. writes the new value to OpenBao kv at secret/postgres/vault-admin
#   4. updates OpenBao's database-engine root config so dynamic creds
#      keep flowing
#   5. runs a connect-smoke as the new password
#
# Run it:
#   - on initial setup (writes the initial password to OpenBao)
#   - on a cadence (e.g. daily cron in production)
#   - whenever the credential might have been exposed
#
# Requires:
#   - OPENBAO_ADDR and OPENBAO_TOKEN (root or a policy with kv put + db config update)
#   - host docker access (to exec into postgres-vault and read current .env pw on first run)
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
OPENBAO_ADDR="${OPENBAO_ADDR:-http://127.0.0.1:8200}"
: "${OPENBAO_TOKEN:?OPENBAO_TOKEN required}"

USERNAME=vault_admin
NEW_PW=$(openssl rand -hex 24)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# First-run bootstrap path: read the current admin pw from .env.
# Once rotation has happened at least once, prefer OpenBao kv.
CURRENT_PW=$(curl -sS -H "X-Vault-Token: $OPENBAO_TOKEN" \
    "$OPENBAO_ADDR/v1/secret/data/postgres/vault-admin" 2>/dev/null \
    | python3 -c 'import sys,json
try:
    d = json.load(sys.stdin); print(d["data"]["data"]["password"])
except Exception:
    print("", end="")' 2>/dev/null || true)

if [ -z "${CURRENT_PW:-}" ]; then
    echo "no prior rotation in OpenBao; bootstrapping from .env"
    CURRENT_PW=$(grep ^POSTGRES_ADMIN_PASSWORD "$REPO/3_storage/postgres/.env" | cut -d= -f2)
fi

echo "rotating Postgres role '$USERNAME' at $NOW"

docker exec -e PGPASSWORD="$CURRENT_PW" postgres-vault psql -U vault_admin -d nhdw_vault \
    -c "ALTER ROLE \"$USERNAME\" WITH LOGIN PASSWORD '$NEW_PW'" >/dev/null

# Write new pw to OpenBao kv
HTTP=$(curl -sS -o /tmp/.bao_rot_resp -w '%{http_code}' \
    -X POST \
    -H "X-Vault-Token: $OPENBAO_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"data\":{\"username\":\"$USERNAME\",\"password\":\"$NEW_PW\",\"rotated_at\":\"$NOW\"}}" \
    "$OPENBAO_ADDR/v1/secret/data/postgres/vault-admin")
case "$HTTP" in
    200|204) echo "wrote new password to OpenBao kv (HTTP $HTTP)" ;;
    *) echo "kv write failed HTTP $HTTP; $(cat /tmp/.bao_rot_resp)" >&2; exit 1 ;;
esac
rm -f /tmp/.bao_rot_resp

# Update OpenBao's Postgres database engine root config so dynamic-cred
# issuance continues to work after this rotation. Engine path must already
# exist (bootstrap.sh creates it).
HTTP=$(curl -sS -o /tmp/.bao_db_resp -w '%{http_code}' \
    -X POST \
    -H "X-Vault-Token: $OPENBAO_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$NEW_PW\"}" \
    "$OPENBAO_ADDR/v1/database/config/postgres-vault" 2>/dev/null || true)
case "$HTTP" in
    200|204) echo "updated OpenBao DB engine root creds (HTTP $HTTP)" ;;
    *)       echo "DB engine root update returned HTTP $HTTP; $(cat /tmp/.bao_db_resp 2>/dev/null || true)" >&2 ;;
esac
rm -f /tmp/.bao_db_resp

# Connect-smoke as the new password
docker exec -e PGPASSWORD="$NEW_PW" postgres-vault psql -U "$USERNAME" -d nhdw_vault \
    -c "SELECT current_user, '$NOW' AS rotated_at" \
    || { echo "post-rotate smoke FAILED" >&2; exit 1; }

echo "rotation complete; secret/postgres/vault-admin updated"
echo
echo "  Next step (to close F-011 fully):"
echo "  1. remove POSTGRES_ADMIN_PASSWORD line from 3_storage/postgres/.env"
echo "  2. callers fetch the password from OpenBao via:"
echo "       bao kv get -field=password secret/postgres/vault-admin"
echo "  3. add this script to a daily cron / Airflow DAG"
