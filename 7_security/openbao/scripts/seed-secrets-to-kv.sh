#!/bin/sh
# Gives every bootstrap secret held in plaintext in a .env file a canonical
# home in OpenBao kv v2 at secret/<domain>/<name>, with a `seeded_at`
# timestamp.
#
# Idempotent and non-destructive:
#   - If the kv entry does not exist, seed it from the .env value.
#   - If the kv entry already exists, leave it alone; the rotation scripts
#     handle any password change after seeding.
#
# After running this script + the matching rotate-*.sh once, the .env
# file's plaintext value is no longer authoritative; consumers should
# fetch from `secret/<path>` (use kv-get.sh).
#
# Called at the end of bootstrap.sh; also safe to run manually.
#
# Requires: BAO_TOKEN (root or a policy with kv put), BAO_ADDR.
set -eu

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
: "${BAO_TOKEN:?BAO_TOKEN required (root or kv-write capable)}"
export BAO_ADDR="${BAO_ADDR:-http://127.0.0.1:8200}"

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Seed one secret into kv if it doesn't exist yet.
#
# Source resolution order for the password value:
#   1. The named env var is already exported in the shell: use it.
#      (This is how the bootstrap container picks up values from its env_file.)
#   2. The env_file path is readable: grep and cut the value out.
#   3. Neither: skip with a SKIP log line.
#
# Args: kv_path  env_file  env_var  extra_keys
seed_if_missing() {
    kv_path=$1
    env_file=$2
    env_var=$3
    extra_keys=$4   # space-separated 'key=value' pairs to attach alongside

    # 1. An already-exported env var wins (works inside the openbao container
    # where env_file pre-populates everything).
    pw=$(printenv "$env_var" 2>/dev/null || true)

    # 2. Fall back to reading the .env file (works from the host).
    if [ -z "$pw" ] && [ -f "$env_file" ]; then
        pw=$(grep "^${env_var}=" "$env_file" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi

    # 3. Nothing found: skip cleanly.
    if [ -z "$pw" ]; then
        echo "  SKIP $kv_path; ${env_var} not in env and $env_file unreadable"
        return 0
    fi

    # Check if already seeded: kv get returns 200 if present.
    HTTP=$(curl -sS -o /dev/null -w '%{http_code}' \
        -H "X-Vault-Token: $BAO_TOKEN" \
        "$BAO_ADDR/v1/secret/data/$kv_path")
    if [ "$HTTP" = "200" ]; then
        echo "  keep  secret/$kv_path  (already seeded; use rotate-*.sh to change)"
        return 0
    fi

    # Build the JSON body. password is always present; extra_keys add
    # username/host/etc that consumers might want alongside.
    data="\"password\":\"$pw\",\"seeded_at\":\"$NOW\",\"source\":\"$env_file::$env_var\""
    for kv in $extra_keys; do
        k=$(echo "$kv" | cut -d= -f1)
        v=$(echo "$kv" | cut -d= -f2-)
        data="$data,\"$k\":\"$v\""
    done

    HTTP=$(curl -sS -o /tmp/.seed_resp -w '%{http_code}' \
        -X POST \
        -H "X-Vault-Token: $BAO_TOKEN" \
        -H 'Content-Type: application/json' \
        -d "{\"data\":{$data}}" \
        "$BAO_ADDR/v1/secret/data/$kv_path")
    case "$HTTP" in
        200|204) echo "  seed  secret/$kv_path  <- $env_file::$env_var" ;;
        *) echo "  FAIL  secret/$kv_path  HTTP $HTTP  $(cat /tmp/.seed_resp 2>/dev/null || true)" >&2; rm -f /tmp/.seed_resp; return 1 ;;
    esac
    rm -f /tmp/.seed_resp
}

echo "[seed] populating OpenBao kv from bootstrap .env files"

# postgres vault_admin (the superuser)
seed_if_missing "postgres/vault-admin" \
    "$REPO/3_storage/postgres/.env" "POSTGRES_ADMIN_PASSWORD" \
    "username=vault_admin database=nhdw_vault host=postgres-vault port=5432"

# postgres landing/writer roles
seed_if_missing "postgres/landing-writer" \
    "$REPO/3_storage/postgres/.env" "LANDING_WRITER_PASSWORD" \
    "username=landing_writer database=nhdw_vault host=postgres-vault port=5432"

seed_if_missing "postgres/vault-writer" \
    "$REPO/3_storage/postgres/.env" "VAULT_WRITER_PASSWORD" \
    "username=vault_writer database=nhdw_vault host=postgres-vault port=5432"

# Keycloak bootstrap admin
seed_if_missing "services/keycloak" \
    "$REPO/7_security/keycloak/.env" "KC_BOOTSTRAP_ADMIN_PASSWORD" \
    "username=keycloak_admin realm=master url=http://keycloak:8090"

# Keycloak DB password
seed_if_missing "services/keycloak-db" \
    "$REPO/7_security/keycloak/.env" "KC_DB_PASSWORD" \
    "username=keycloak database=keycloak host=keycloak-db port=5432"

# Airflow admin and Fernet key
seed_if_missing "services/airflow" \
    "$REPO/5_orchestrator/.env" "AIRFLOW_ADMIN_PASSWORD" \
    "username=admin realm=airflow"

seed_if_missing "services/airflow-fernet" \
    "$REPO/5_orchestrator/.env" "AIRFLOW_FERNET_KEY" \
    "key_use=airflow-fernet"

# Grafana admin

seed_if_missing "services/grafana" \
    "$REPO/4_monitoring/.env" "GRAFANA_ADMIN_PASSWORD" \
    "username=admin"

# ClickHouse staging admin
seed_if_missing "services/clickhouse" \
    "$REPO/3_storage/clickhouse/.env" "CLICKHOUSE_STAGING_PASSWORD" \
    "username=staging_admin database=nhdw_staging"

echo "[seed] DONE"
echo
echo "  To CLOSE F-005 / F-011 fully, run the rotation scripts now and"
echo "  remove the matching .env lines:"
echo
echo "    bash 7_security/openbao/scripts/rotate-postgres-vault-admin.sh"
echo "    KC_OLD_PASSWORD=\$(bao kv get -field=password secret/services/keycloak) \\"
echo "    sh   7_security/openbao/scripts/rotate-keycloak-admin.sh"
echo
echo "  Consumers fetch from kv via:"
echo "    bao kv get -field=password secret/postgres/vault-admin"
echo "    bao kv get -field=password secret/services/keycloak"
