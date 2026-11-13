#!/bin/bash
# Creates the `seaweedfs_filer` database that backs the SeaweedFS filer (S3 key
# to chunk metadata). Object bytes live on the volume server's PVC; this
# database maps keys to those bytes. Losing it orphans every object, so it lives
# inside the postgres-vault backup path instead of in an embedded LevelDB on the
# filer pod.
#
# Two constraints (tested in 3_storage/seaweedfs/poc/README.md):
#   1. SeaweedFS does not create its table. A filer started against an empty
#      database fails with `relation "filemeta" does not exist`, so it is
#      created here.
#   2. `name`/`directory` must be COLLATE "C". Under the cluster's en_US.utf8
#      default the filer warns that S3 list order is not byte-lexicographic and
#      falls back to a slower sort; clients that merge sorted listings then see
#      phantom differences.
#
# Idempotent. Runs automatically on a fresh postgres-vault (initdb). On an
# existing cluster, where initdb does not re-run, apply it by hand:
#   kubectl -n nhdw exec -i postgres-vault-0 -- env \
#       SEAWEEDFS_FILER_DB_PASSWORD="$(kubectl -n nhdw get secret seaweedfs-secrets -o jsonpath='{.data.FILER_DB_PASSWORD}' | base64 -d)" \
#       bash < 3_storage/postgres/init/23_seaweedfs_filer_db.sh
set -euo pipefail

required_vars=(SEAWEEDFS_FILER_DB_PASSWORD)
for v in "${required_vars[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "[23_seaweedfs_filer_db] FATAL: env var $v is empty" >&2
        exit 1
    fi
done

FILER_DB="${SEAWEEDFS_FILER_DB_NAME:-seaweedfs_filer}"
FILER_ROLE="${SEAWEEDFS_FILER_DB_USER:-seaweedfs_filer}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
SELECT 'CREATE DATABASE $FILER_DB OWNER $POSTGRES_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$FILER_DB')\gexec

COMMENT ON DATABASE $FILER_DB IS
    'SeaweedFS filer metadata store: S3 key -> chunk map for the lake. Object bytes live on the seaweedfs-volume PVC. Table filemeta MUST stay COLLATE "C".';

DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$FILER_ROLE') THEN
        CREATE ROLE $FILER_ROLE LOGIN PASSWORD '$SEAWEEDFS_FILER_DB_PASSWORD';
    ELSE
        ALTER ROLE $FILER_ROLE WITH PASSWORD '$SEAWEEDFS_FILER_DB_PASSWORD';
    END IF;
END\$\$;

GRANT CONNECT ON DATABASE $FILER_DB TO $FILER_ROLE;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$FILER_DB" <<SQL
-- Schema from the SeaweedFS postgres store, with the "C" collation fix on name.
CREATE TABLE IF NOT EXISTS filemeta (
    dirhash   BIGINT,
    name      VARCHAR(65535) COLLATE "C",
    directory VARCHAR(65535) COLLATE "C",
    meta      bytea,
    PRIMARY KEY (dirhash, name)
);

GRANT ALL ON SCHEMA public TO $FILER_ROLE;
GRANT ALL PRIVILEGES ON TABLE filemeta TO $FILER_ROLE;
ALTER TABLE filemeta OWNER TO $FILER_ROLE;

-- Same lesson as 09_mlflow_db.sh: a pg_restore as the superuser re-owns tables,
-- so grant on whatever exists now AND set default privileges for later restores.
GRANT ALL ON ALL TABLES    IN SCHEMA public TO $FILER_ROLE;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO $FILER_ROLE;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO $FILER_ROLE;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $FILER_ROLE;

-- Guard rail: refuse to proceed if someone recreated the table without COLLATE "C".
DO \$\$
DECLARE bad int;
BEGIN
    SELECT count(*) INTO bad FROM information_schema.columns
     WHERE table_name = 'filemeta' AND column_name IN ('name','directory')
       AND collation_name IS DISTINCT FROM 'C';
    IF bad > 0 THEN
        RAISE EXCEPTION 'filemeta.name/directory must be COLLATE "C" (see 3_storage/seaweedfs/poc/README.md)';
    END IF;
END\$\$;
SQL

echo "[23_seaweedfs_filer_db] $FILER_DB provisioned · $FILER_ROLE ready · filemeta COLLATE \"C\" verified"
