#!/bin/bash
# Creates the mlflow_metadata schema and mlflow user in a local postgres-vault
# container (local setup; Kubernetes uses 3_storage/postgres/init/09_mlflow_db.sh).
# Idempotent.
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (cp .env.example .env)" >&2; exit 1; }
. ./.env

. ../postgres/.env

docker exec -e PGPASSWORD="$POSTGRES_ADMIN_PASSWORD" postgres-vault \
    psql -h localhost -U vault_admin -d nhdw_vault -v ON_ERROR_STOP=1 -c "
DO \$\$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mlflow') THEN
        CREATE ROLE mlflow WITH LOGIN PASSWORD '$MLFLOW_DB_PASSWORD';
    ELSE
        ALTER ROLE mlflow WITH PASSWORD '$MLFLOW_DB_PASSWORD';
    END IF;
END \$\$;
CREATE SCHEMA IF NOT EXISTS mlflow_metadata AUTHORIZATION mlflow;
GRANT ALL PRIVILEGES ON SCHEMA mlflow_metadata TO mlflow;
"
echo "[init-pg] mlflow user + mlflow_metadata schema ready"
