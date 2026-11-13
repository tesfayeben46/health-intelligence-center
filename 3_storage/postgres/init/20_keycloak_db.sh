#!/bin/bash
# Creates the keycloak Postgres role and the keycloak_metadata schema. Runs
# on a fresh postgres-vault boot (docker-entrypoint-initdb.d). Idempotent.
# KEYCLOAK_DB_PASSWORD comes from the container env (the k8s deployment and
# the docker-compose env_file both set it).
set -euo pipefail
: "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD env not set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keycloak') THEN
    CREATE ROLE keycloak WITH LOGIN PASSWORD '$KEYCLOAK_DB_PASSWORD';
  ELSE
    ALTER ROLE keycloak WITH PASSWORD '$KEYCLOAK_DB_PASSWORD';
  END IF;
END \$\$;
CREATE SCHEMA IF NOT EXISTS keycloak_metadata AUTHORIZATION keycloak;
GRANT ALL PRIVILEGES ON SCHEMA keycloak_metadata TO keycloak;
EOSQL
echo "[20_keycloak_db] keycloak user + keycloak_metadata schema ready"
