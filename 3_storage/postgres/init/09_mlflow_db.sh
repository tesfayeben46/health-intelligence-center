#!/bin/bash
# Creates the `nhdw_mlflow` database for the MLflow tracking server (run
# metadata, model registry, experiment metadata). Artifacts (model weights,
# plots) go to the object store under nhdw-artifacts/mlflow/.
#
# MLflow migrates its own schema on startup, so this script only creates the
# database and a writer role. Idempotent.
set -euo pipefail

required_vars=(MLFLOW_DB_PASSWORD)
for v in "${required_vars[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "[09_mlflow_db] FATAL: env var $v is empty" >&2
        exit 1
    fi
done

OPS_DB="${MLFLOW_DB_NAME:-nhdw_mlflow}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
SELECT 'CREATE DATABASE $OPS_DB OWNER $POSTGRES_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$OPS_DB')\gexec

COMMENT ON DATABASE $OPS_DB IS
    'MLflow tracking-server metadata: runs, params, metrics, model registry, stages. Artifacts live in MinIO nhdw-artifacts/mlflow/.';

DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mlflow_writer') THEN
        CREATE ROLE mlflow_writer LOGIN PASSWORD '$MLFLOW_DB_PASSWORD';
    ELSE
        ALTER ROLE mlflow_writer WITH PASSWORD '$MLFLOW_DB_PASSWORD';
    END IF;
END\$\$;

GRANT ALL PRIVILEGES ON DATABASE $OPS_DB TO mlflow_writer;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<SQL
GRANT ALL ON SCHEMA public TO mlflow_writer;
ALTER SCHEMA public OWNER TO mlflow_writer;

-- MLflow's tables are not always created by mlflow_writer: restore-state.sh
-- pg_restores the packed state as $POSTGRES_USER with --no-owner, so every
-- restored table lands owned by the superuser and mlflow_writer loses all
-- table privileges ("permission denied for table registered_models" on the
-- registry REST API). Fix both directions: grant on whatever exists now, and
-- set default privileges so tables the superuser creates later (another
-- restore) are granted automatically.
GRANT ALL ON ALL TABLES    IN SCHEMA public TO mlflow_writer;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO mlflow_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO mlflow_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO mlflow_writer;
SQL

echo "[09_mlflow_db] $OPS_DB provisioned · mlflow_writer ready"
