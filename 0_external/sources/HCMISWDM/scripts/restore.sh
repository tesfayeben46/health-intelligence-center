#!/usr/bin/env bash
# Restore the HCMISWDM .bak into the running SQL Server container and provision
# the read-only service users that the ingestor and OpenFn jobs use.
#
# Prereq: containers running (`docker compose up -d`)
# Usage:  bash scripts/restore.sh

set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "ERROR: .env not found. Run: cp .env.example .env" >&2
  exit 1
fi

CONTAINER=hcmiswdm-sqlserver
BAK_SRC="sample-data/HCMISWDM.bak"
BAK_IN_CONTAINER=/var/opt/mssql/backup/HCMISWDM.bak

SA_PASS="${HCMISWDM_SA_PASSWORD:?HCMISWDM_SA_PASSWORD must be set}"
READER_PASS="${HCMISWDM_NSCD_READER_PASSWORD:?HCMISWDM_NSCD_READER_PASSWORD must be set}"
OPENFN_PASS="${HCMISWDM_OPENFN_READER_PASSWORD:?HCMISWDM_OPENFN_READER_PASSWORD must be set}"

SQLCMD="docker exec -i ${CONTAINER} /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P ${SA_PASS} -C -b"

if [ ! -e "$BAK_SRC" ]; then
  echo "ERROR: backup missing at $BAK_SRC (symlink target may be unavailable)" >&2
  exit 1
fi

echo "==> Waiting for $CONTAINER to be healthy..."
for _ in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "starting")
  if [ "$status" = "healthy" ]; then
    break
  fi
  sleep 3
done
if [ "$status" != "healthy" ]; then
  echo "ERROR: $CONTAINER did not reach healthy status. Logs:" >&2
  docker logs --tail 30 "$CONTAINER" >&2
  exit 1
fi

echo "==> Copying backup into container..."
docker cp "$(readlink -f "$BAK_SRC")" "${CONTAINER}:${BAK_IN_CONTAINER}"
# docker cp preserves host ownership. SQL Server runs as the non-root mssql
# user (uid 10001) and cannot read a root-owned file. Fix the ownership.
docker exec -u root "$CONTAINER" chown mssql:0 "$BAK_IN_CONTAINER"
docker exec -u root "$CONTAINER" chmod 660 "$BAK_IN_CONTAINER"

echo "==> Restoring HCMISWDM database..."
# Derive the logical file names from the backup itself: they follow the
# backup's lineage, not the database name (e.g. HCMISWDMNEXT / HCMISWDMNEXT_Log),
# and a hardcoded MOVE 'HCMISWDM' fails with Msg 3234.
DATA_LOGICAL=$(${SQLCMD} -h -1 -W -Q "SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK='${BAK_IN_CONTAINER}'" | awk '$0 ~ /\/var\/opt\/mssql/ && $2 ~ /\.mdf$/ {print $1; exit}')
LOG_LOGICAL=$(${SQLCMD} -h -1 -W -Q "SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK='${BAK_IN_CONTAINER}'" | awk '$0 ~ /\/var\/opt\/mssql/ && $2 ~ /\.ldf$/ {print $1; exit}')
echo "    logical files: data='${DATA_LOGICAL}' log='${LOG_LOGICAL}'"
${SQLCMD} -Q "
RESTORE DATABASE [HCMISWDM]
FROM DISK = '${BAK_IN_CONTAINER}'
WITH
    MOVE '${DATA_LOGICAL}' TO '/var/opt/mssql/data/HCMISWDM.mdf',
    MOVE '${LOG_LOGICAL}'  TO '/var/opt/mssql/data/HCMISWDM_log.ldf',
    REPLACE,
    STATS = 10
"

echo "==> Provisioning read-only service users..."

${SQLCMD} -Q "
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'nscd_reader')
    CREATE LOGIN [nscd_reader]
        WITH PASSWORD = N'${READER_PASS}',
             CHECK_POLICY = OFF,
             CHECK_EXPIRATION = OFF;
ALTER LOGIN [nscd_reader] WITH PASSWORD = N'${READER_PASS}';
"
${SQLCMD} -d HCMISWDM -Q "
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'nscd_reader')
    CREATE USER [nscd_reader] FOR LOGIN [nscd_reader];
ELSE
    -- A packed .bak carries the DB user with the OLD login SID (orphaned once
    -- the login above is recreated) -> re-map it to the current login.
    ALTER USER [nscd_reader] WITH LOGIN = [nscd_reader];
ALTER ROLE db_datareader ADD MEMBER [nscd_reader];
DENY INSERT, UPDATE, DELETE, EXECUTE ON DATABASE::[HCMISWDM] TO [nscd_reader];
"

${SQLCMD} -Q "
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'openfn_nscd')
    CREATE LOGIN [openfn_nscd]
        WITH PASSWORD = N'${OPENFN_PASS}',
             CHECK_POLICY = OFF,
             CHECK_EXPIRATION = OFF;
ALTER LOGIN [openfn_nscd] WITH PASSWORD = N'${OPENFN_PASS}';
"
${SQLCMD} -d HCMISWDM -Q "
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'openfn_nscd')
    CREATE USER [openfn_nscd] FOR LOGIN [openfn_nscd];
ELSE
    ALTER USER [openfn_nscd] WITH LOGIN = [openfn_nscd];   -- fix orphan after .bak restore
ALTER ROLE db_datareader ADD MEMBER [openfn_nscd];
DENY INSERT, UPDATE, DELETE, EXECUTE ON DATABASE::[HCMISWDM] TO [openfn_nscd];
"

echo "==> Removing backup from container..."
# Best-effort removal of the in-container copy (sample-data/*.bak is copied in
# fresh on each run). Run as root: the container's default user (mssql) cannot
# remove it, and cleanup must never fail a successful restore.
docker exec -u root "$CONTAINER" rm -f "$BAK_IN_CONTAINER" || true

echo "==> Table inventory (showing schemas + row counts; expect 0 rows pre-synthesis)..."
${SQLCMD} -d HCMISWDM -Q "
SELECT
    s.name  AS schema_name,
    t.name  AS table_name,
    p.rows  AS row_count
FROM sys.tables     t
JOIN sys.schemas    s ON t.schema_id   = s.schema_id
JOIN sys.partitions p ON t.object_id   = p.object_id
                     AND p.index_id IN (0, 1)
WHERE s.name IN ('Vaccine','DM','Commodity','Fact','Dim','StageThree','Staging')
ORDER BY s.name, t.name;
"

echo ""
echo "OK: schema restore complete."
echo "Next step: synthesize realistic vaccine-supply-chain row data:"
echo "  python3 scripts/synthesize.py"
