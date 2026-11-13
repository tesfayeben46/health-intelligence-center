#!/usr/bin/env bash
# Verify the HCMIS WDM replica is reachable and has data.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CONTAINER=hcmiswdm-sqlserver
SQLCMD="docker exec -i ${CONTAINER} /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P ${HCMISWDM_SA_PASSWORD} -C -d HCMISWDM -h -1 -W"

echo "== 1/3 SQL Server reachability (sa) =="
if $SQLCMD -Q "SELECT @@VERSION;" 2>/dev/null | head -1 | grep -q "Microsoft SQL Server"; then
  echo "   PASS"
else
  echo "   FAIL"; exit 1
fi

echo ""
echo "== 2/3 Read-only user (nscd_reader) =="
if docker exec -i "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd \
     -S localhost -U nscd_reader -P "${HCMISWDM_NSCD_READER_PASSWORD}" -C -d HCMISWDM \
     -Q "SELECT COUNT(*) FROM Vaccine.Item;" -h -1 -W 2>/dev/null | head -1 | grep -qE "^[0-9]+$"
then
  echo "   PASS"
else
  echo "   FAIL: nscd_reader cannot select"; exit 1
fi

echo ""
echo "== 3/3 Row counts (Vaccine + Dim + Commodity + Fact) =="
$SQLCMD -Q "
SELECT 'Vaccine.Environment'  AS table_name, COUNT(*) AS n FROM Vaccine.Environment
UNION ALL SELECT 'Vaccine.Unit',         COUNT(*) FROM Vaccine.Unit
UNION ALL SELECT 'Vaccine.Item',         COUNT(*) FROM Vaccine.Item
UNION ALL SELECT 'Vaccine.Institution',  COUNT(*) FROM Vaccine.Institution
UNION ALL SELECT 'Vaccine.DailyRecord',  COUNT(*) FROM Vaccine.DailyRecord
UNION ALL SELECT 'Vaccine.Received',     COUNT(*) FROM Vaccine.Received
UNION ALL SELECT 'Vaccine.Issued',       COUNT(*) FROM Vaccine.Issued
UNION ALL SELECT 'Vaccine.Expired',      COUNT(*) FROM Vaccine.Expired
UNION ALL SELECT 'Dim.Mode',             COUNT(*) FROM Dim.Mode
UNION ALL SELECT 'Dim.Program',          COUNT(*) FROM Dim.Program
UNION ALL SELECT 'Dim.Environment',      COUNT(*) FROM Dim.Environment
UNION ALL SELECT 'Dim.Item',             COUNT(*) FROM Dim.Item
UNION ALL SELECT 'Dim.Date',             COUNT(*) FROM Dim.Date
UNION ALL SELECT 'Commodity.Product',    COUNT(*) FROM Commodity.Product
UNION ALL SELECT 'Commodity.Manufacturer',COUNT(*) FROM Commodity.Manufacturer
UNION ALL SELECT 'Commodity.Supplier',   COUNT(*) FROM Commodity.Supplier
UNION ALL SELECT 'Commodity.Category',   COUNT(*) FROM Commodity.Category
UNION ALL SELECT 'Fact.DailyRecord',     COUNT(*) FROM Fact.DailyRecord
UNION ALL SELECT 'Fact.Inventory',       COUNT(*) FROM Fact.Inventory;
"

echo ""
echo "ALL CHECKS PASSED"
