#!/usr/bin/env bash
# Restore HCMISWCloud.bak into the shared hcmiswdm-sqlserver container and
# provision the read-only service users the ingestor uses.
#
# Prereq: the HCMISWDM stack running (`docker compose up -d` in ../HCMISWDM)
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

CONTAINER=hcmiswdm-sqlserver   # shared instance: mBrana has no compose of its own (see the HCMISWDM compose header)
BAK_SRC="sample-data/HCMISWCloud.bak"
BAK_IN_CONTAINER=/var/opt/mssql/backup/HCMISWCloud.bak

SA_PASS="${MBRANA_SA_PASSWORD:?MBRANA_SA_PASSWORD must be set}"
READER_PASS="${MBRANA_READER_PASSWORD:?MBRANA_READER_PASSWORD must be set}"
OPENFN_PASS="${MBRANA_OPENFN_PASSWORD:?MBRANA_OPENFN_PASSWORD must be set}"
DB="${MBRANA_DATABASE:-HCMISWCloud}"

SQLCMD="docker exec -i ${CONTAINER} /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P ${SA_PASS} -C -b"

if [ ! -e "$BAK_SRC" ]; then
  echo "ERROR: backup missing at $BAK_SRC (symlink target may be unavailable)" >&2
  exit 1
fi

echo "==> Waiting for $CONTAINER to be healthy..."
for _ in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "starting")
  if [ "$status" = "healthy" ]; then break; fi
  sleep 3
done
if [ "$status" != "healthy" ]; then
  echo "ERROR: $CONTAINER did not reach healthy status." >&2
  docker logs --tail 30 "$CONTAINER" >&2
  exit 1
fi

echo "==> Copying backup into container..."
docker cp "$(readlink -f "$BAK_SRC")" "${CONTAINER}:${BAK_IN_CONTAINER}"
docker exec -u root "$CONTAINER" chown mssql:0 "$BAK_IN_CONTAINER"
docker exec -u root "$CONTAINER" chmod 660 "$BAK_IN_CONTAINER"

echo "==> Restoring HCMISWCloud database..."
${SQLCMD} -Q "
RESTORE DATABASE [$DB]
FROM DISK = '${BAK_IN_CONTAINER}'
WITH
    MOVE 'HCMISWCloud'     TO '/var/opt/mssql/data/HCMISWCloud.mdf',
    MOVE 'HCMISWCloud_log' TO '/var/opt/mssql/data/HCMISWCloud_log.ldf',
    REPLACE, STATS = 10
"

echo "==> Provisioning read-only service users..."

# ingestor: internal Airflow/dbt extraction
${SQLCMD} -Q "
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'ingestor')
    CREATE LOGIN [ingestor]
        WITH PASSWORD = N'${READER_PASS}',
             CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF;
ALTER LOGIN [ingestor] WITH PASSWORD = N'${READER_PASS}';
"
${SQLCMD} -d "$DB" -Q "
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'ingestor')
    CREATE USER [ingestor] FOR LOGIN [ingestor];
ELSE
    -- The restored .bak carries the DB user with the OLD login SID (orphaned
    -- once the login above is recreated on a fresh server) -> re-map it. This is
    -- the canonical SQL Server 'restore to a new server' orphaned-user fix.
    ALTER USER [ingestor] WITH LOGIN = [ingestor];
ALTER ROLE db_datareader ADD MEMBER [ingestor];
DENY INSERT, UPDATE, DELETE, EXECUTE ON DATABASE::[${DB}] TO [ingestor];
"

# openfn: external OpenFn ingestion through an SSH tunnel
${SQLCMD} -Q "
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'openfn_mbrana')
    CREATE LOGIN [openfn_mbrana]
        WITH PASSWORD = N'${OPENFN_PASS}',
             CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF;
ALTER LOGIN [openfn_mbrana] WITH PASSWORD = N'${OPENFN_PASS}';
"
${SQLCMD} -d "$DB" -Q "
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'openfn_mbrana')
    CREATE USER [openfn_mbrana] FOR LOGIN [openfn_mbrana];
ELSE
    ALTER USER [openfn_mbrana] WITH LOGIN = [openfn_mbrana];   -- re-map orphan after restore
ALTER ROLE db_datareader ADD MEMBER [openfn_mbrana];
DENY INSERT, UPDATE, DELETE, EXECUTE ON DATABASE::[${DB}] TO [openfn_mbrana];
"

echo "==> Cleanup..."
# Best-effort removal of the in-container copy (sample-data/*.bak is copied in
# fresh on each run). Run as root: the container's default user (mssql) cannot
# remove it, and cleanup must never fail a successful restore.
docker exec -u root "$CONTAINER" rm -f "$BAK_IN_CONTAINER" || true

echo "==> Schema inventory ..."
${SQLCMD} -d "$DB" -h -1 -W -Q "
SELECT s.name + ' schema: ' + CAST(COUNT(*) AS VARCHAR(8)) + ' tables'
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
WHERE s.name IN ('mBrana','mFlowUSSD','RRF','AdministrativeUnit','Institution','Commodity','USM','dbo')
GROUP BY s.name ORDER BY s.name;
"

echo ""
echo "OK: schema restore complete."
echo "Next step: synthesize realistic Ethiopian row data:"
echo "  python3 scripts/synthesize.py"
