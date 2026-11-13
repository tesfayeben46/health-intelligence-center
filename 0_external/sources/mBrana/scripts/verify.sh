#!/usr/bin/env bash
# Verify the mBrana replica is reachable and has data.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then set -a; source .env; set +a; fi

CONTAINER=hcmiswdm-sqlserver   # shared instance: mBrana has no compose of its own (see the HCMISWDM compose header)
DB="${MBRANA_DATABASE:-HCMISWCloud}"
SQLCMD="docker exec -i ${CONTAINER} /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P ${MBRANA_SA_PASSWORD} -C -d ${DB} -h -1 -W"

fail=0

echo "== 1/3 SQL Server reachability (sa) =="
if $SQLCMD -Q "SELECT @@VERSION;" 2>/dev/null | head -1 | grep -q "Microsoft SQL Server"; then
  echo "   PASS"
else
  echo "   FAIL"; fail=1
fi

echo ""
echo "== 2/3 Read-only user (ingestor) =="
if docker exec -i "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd \
     -S localhost -U ingestor -P "${MBRANA_READER_PASSWORD}" -C -d "$DB" \
     -Q "SELECT COUNT(*) FROM dbo.Institution;" -h -1 -W 2>/dev/null | head -1 | grep -qE "^[0-9]+$"
then
  echo "   PASS"
else
  echo "   FAIL: ingestor cannot select"; fail=1
fi

echo ""
echo "== 3/3 Row counts =="
$SQLCMD -Q "
SELECT 'AdministrativeUnit.AdministrativeUnit'  AS tbl, COUNT(*) AS n FROM AdministrativeUnit.AdministrativeUnit
UNION ALL SELECT 'dbo.Region',                         COUNT(*) FROM dbo.Region
UNION ALL SELECT 'dbo.Zone',                           COUNT(*) FROM dbo.Zone
UNION ALL SELECT 'dbo.Woreda',                         COUNT(*) FROM dbo.Woreda
UNION ALL SELECT 'dbo.Institution',                    COUNT(*) FROM dbo.Institution
UNION ALL SELECT 'USM.Environment',                    COUNT(*) FROM USM.Environment
UNION ALL SELECT 'Commodity.Product',                  COUNT(*) FROM Commodity.Product
UNION ALL SELECT 'mBrana.InstitutionEnvironment',      COUNT(*) FROM mBrana.InstitutionEnvironment
UNION ALL SELECT 'mFlowUSSD.SOHLogs',                  COUNT(*) FROM mFlowUSSD.SOHLogs
UNION ALL SELECT 'mFlowUSSD.Requests',                 COUNT(*) FROM mFlowUSSD.Requests
UNION ALL SELECT 'mFlowUSSD.RequestLines',             COUNT(*) FROM mFlowUSSD.RequestLines
UNION ALL SELECT 'mFlowUSSD.ASNs',                     COUNT(*) FROM mFlowUSSD.ASNs;
"

echo ""
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else exit 1; fi
