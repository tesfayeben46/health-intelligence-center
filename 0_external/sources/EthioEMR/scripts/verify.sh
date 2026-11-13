#!/usr/bin/env bash
# Verify the EthioEMR replica on the interfaces a real EthioEMR exposes:
# MySQL (CDC / direct DB), OpenMRS REST and FHIR R4.
#
# The MySQL checks always run. The REST and FHIR checks are soft: this replica
# does not run the OpenMRS web app, so they report SKIP unless
# OPENMRS_REQUIRED=1 makes them hard failures.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

OPENMRS_REQUIRED="${OPENMRS_REQUIRED:-0}"
OPENMRS_BASE="${ETHIOEMR_OPENMRS_BASE_URL:-http://127.0.0.1:8081/openmrs}"
ROOT_PW="${ETHIOEMR_MYSQL_ROOT_PASSWORD}"

fail=0

echo "== 1/4 MySQL reachability (ingestor user) =="
if docker exec -i ethioemr-mysql mysql -uingestor -p"${ETHIOEMR_READER_PASSWORD}" \
     openmrs -e "SELECT 'mysql up' AS status;" 2>/dev/null | grep -q "mysql up"
then
  echo "   PASS"
else
  echo "   FAIL: MySQL or ingestor user not ready"; fail=1
fi

echo ""
echo "== 2/4 Clinical row counts =="
docker exec -i ethioemr-mysql mysql -uroot -p"${ROOT_PW}" openmrs -e "
SELECT 'location'  AS t, COUNT(*) AS n FROM location
UNION ALL SELECT 'provider',          COUNT(*) FROM provider
UNION ALL SELECT 'patient',           COUNT(*) FROM patient
UNION ALL SELECT 'patient_identifier',COUNT(*) FROM patient_identifier
UNION ALL SELECT 'visit',             COUNT(*) FROM visit
UNION ALL SELECT 'encounter',         COUNT(*) FROM encounter
UNION ALL SELECT 'obs',               COUNT(*) FROM obs
UNION ALL SELECT 'concept',           COUNT(*) FROM concept;
" 2>/dev/null

# Soft REST / FHIR checks
echo ""
echo "== 3/4 OpenMRS REST (soft check) =="
if curl -fsS "$OPENMRS_BASE/ws/rest/v1/session" -u admin:Admin123 -o /dev/null 2>/dev/null; then
  echo "   PASS: /ws/rest/v1/session responds"
else
  echo "   SKIP: OpenMRS web container not running (set OPENMRS_REQUIRED=1 to enforce)"
  [ "$OPENMRS_REQUIRED" = "1" ] && fail=1
fi

echo ""
echo "== 4/4 FHIR R4 capability (soft check) =="
if curl -fsS "$OPENMRS_BASE/ws/fhir2/R4/metadata" -u admin:Admin123 -o /dev/null 2>/dev/null; then
  echo "   PASS: FHIR R4 capability statement available"
else
  echo "   SKIP: OpenMRS web not up"
  [ "$OPENMRS_REQUIRED" = "1" ] && fail=1
fi

echo ""
if [ "$fail" -eq 0 ]; then echo "ALL HARD CHECKS PASSED"; else echo "ONE OR MORE FAILED"; exit 1; fi
