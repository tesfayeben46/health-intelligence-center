#!/bin/bash
# Creates the `nhdw_openmetadata` database for the OpenMetadata catalog server
# (entities, lineage graph, glossary, ingestion state). OpenMetadata owns it
# entirely; nothing else reads it.
#
# Same arrangement as nhdw_superset, nhdw_mlflow and nhdw_airflow: one Postgres
# to operate, isolation at the database boundary. OpenMetadata runs its own
# migrations at start-up (db-migrate initContainer in
# 1_infrastructure/k8s/base/openmetadata.yaml).
#
# OPENMETADATA_DB_PASSWORD comes from the `openmetadata-secrets` k8s Secret
# (rendered from 1_infrastructure/k8s/overlays/dev/secrets.yaml.template).
# Idempotent.
set -euo pipefail

required_vars=(OPENMETADATA_DB_PASSWORD)
for v in "${required_vars[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "[37_openmetadata_db] FATAL: env var $v is empty" >&2
        exit 1
    fi
done

OM_DB="${OPENMETADATA_DB_NAME:-nhdw_openmetadata}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
SELECT 'CREATE DATABASE $OM_DB OWNER $POSTGRES_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$OM_DB')\gexec

COMMENT ON DATABASE $OM_DB IS
    'OpenMetadata catalog DB: entities, lineage, glossary, ingestion state. Owned by openmetadata_writer; nothing else reads it.';

DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openmetadata_writer') THEN
        CREATE ROLE openmetadata_writer LOGIN PASSWORD '$OPENMETADATA_DB_PASSWORD';
    ELSE
        ALTER ROLE openmetadata_writer WITH PASSWORD '$OPENMETADATA_DB_PASSWORD';
    END IF;
END\$\$;

GRANT ALL PRIVILEGES ON DATABASE $OM_DB TO openmetadata_writer;
SQL

# Inside the new DB, grant schema CREATE so OM can run its migrations.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OM_DB" <<SQL
GRANT ALL ON SCHEMA public TO openmetadata_writer;
ALTER SCHEMA public OWNER TO openmetadata_writer;
SQL

echo "[37_openmetadata_db] $OM_DB provisioned · openmetadata_writer role ready"
