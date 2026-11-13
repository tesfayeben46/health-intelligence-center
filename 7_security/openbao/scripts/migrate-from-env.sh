#!/bin/sh
# Migrate critical secrets from existing .env files into OpenBao kv.
#
# Reads from the host-side .env files (mounted as /repo) and writes to
# secret/data/services/<name>. After this runs successfully, the .env
# files remain as a fallback but new services should read from OpenBao.
#
# Idempotent: re-running overwrites the kv entries with current .env
# values. Existing OIDC-bound clients keep working until their leases
# rotate to the new path.
set -eu

export BAO_ADDR="${BAO_ADDR:-http://127.0.0.1:8200}"
export BAO_TOKEN="${BAO_TOKEN:-$(cat /var/lib/openbao/root-token)}"

get() {
    # Read a key from a .env file. Tolerates 'KEY=value' with no quoting.
    local file="$1" key="$2"
    grep "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-
}

put() {
    # bao kv put silences output; trim trailing slashes.
    local path="$1"; shift
    bao kv put "secret/services/$path" "$@" > /dev/null
    echo "[migrate]   secret/services/$path"
}

echo "[migrate] reading .env files from /repo and writing to OpenBao"


# clickhouse
if [ -f /repo/3_storage/clickhouse/.env ]; then
    put clickhouse \
        staging_password="$(get /repo/3_storage/clickhouse/.env CLICKHOUSE_STAGING_PASSWORD)" \
        marts_password="$(get /repo/3_storage/clickhouse/.env CLICKHOUSE_MARTS_PASSWORD)" \
        reader_password="$(get /repo/3_storage/clickhouse/.env CLICKHOUSE_READER_PASSWORD)"
fi

# postgres-vault
if [ -f /repo/3_storage/postgres/.env ]; then
    put postgres-vault \
        admin_password="$(get /repo/3_storage/postgres/.env POSTGRES_ADMIN_PASSWORD)" \
        landing_writer_password="$(get /repo/3_storage/postgres/.env LANDING_WRITER_PASSWORD)" \
        vault_writer_password="$(get /repo/3_storage/postgres/.env VAULT_WRITER_PASSWORD)" \
        bi_reader_password="$(get /repo/3_storage/postgres/.env BI_READER_PASSWORD)"
fi

# airflow
if [ -f /repo/5_orchestrator/.env ]; then
    put airflow \
        admin_user="$(get /repo/5_orchestrator/.env AIRFLOW_ADMIN_USER)" \
        admin_password="$(get /repo/5_orchestrator/.env AIRFLOW_ADMIN_PASSWORD)" \
        postgres_password="$(get /repo/5_orchestrator/.env AIRFLOW_POSTGRES_PASSWORD)"
fi

# keycloak (bootstrap admin credentials)
if [ -f /repo/7_security/keycloak/.env ]; then
    put keycloak \
        bootstrap_admin_user="$(get /repo/7_security/keycloak/.env KC_BOOTSTRAP_ADMIN_USERNAME)" \
        bootstrap_admin_password="$(get /repo/7_security/keycloak/.env KC_BOOTSTRAP_ADMIN_PASSWORD)" \
        db_password="$(get /repo/7_security/keycloak/.env KC_DB_PASSWORD)"
fi

# cloudbeaver
if [ -f /repo/6_access-points/dashboards/database-dashboard/.env ]; then
    put cloudbeaver \
        admin_user="$(get /repo/6_access-points/dashboards/database-dashboard/.env CLOUDBEAVER_ADMIN_USER)" \
        admin_password="$(get /repo/6_access-points/dashboards/database-dashboard/.env CLOUDBEAVER_ADMIN_PASSWORD)"
fi

# source DBs (read-only consumers will use OpenBao to learn these)
if [ -f /repo/0_external/sources/EthioEMR/.env ]; then
    put ethioemr \
        db_user="$(get /repo/0_external/sources/EthioEMR/.env OPENMRS_DB_USER)" \
        db_password="$(get /repo/0_external/sources/EthioEMR/.env OPENMRS_DB_PASSWORD)"
fi
if [ -f /repo/0_external/sources/HCMISWDM/.env ]; then
    put hcmiswdm \
        sa_password="$(get /repo/0_external/sources/HCMISWDM/.env MSSQL_SA_PASSWORD)" \
        reader_user="$(get /repo/0_external/sources/HCMISWDM/.env READER_USER)" \
        reader_password="$(get /repo/0_external/sources/HCMISWDM/.env READER_PASSWORD)"
fi
if [ -f /repo/0_external/sources/mBrana/.env ]; then
    put mbrana \
        sa_password="$(get /repo/0_external/sources/mBrana/.env MBRANA_SA_PASSWORD)"
fi
if [ -f /repo/0_external/sources/DHIS2/.env ]; then
    put dhis2 \
        user="$(get /repo/0_external/sources/DHIS2/.env DHIS2_USER)" \
        password="$(get /repo/0_external/sources/DHIS2/.env DHIS2_PASSWORD)"
fi

echo "[migrate] done, listing what's now in secret/services/"
bao kv list secret/services/ 2>&1 | sed 's/^/  /'
