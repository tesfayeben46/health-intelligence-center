#!/usr/bin/env bash
# Verify the DHIS2 replica exposes the API surface the ingestor consumes.
#
# The ingestor extracts raw values and metadata; aggregates are computed
# downstream in dbt. This script therefore checks raw and metadata endpoints
# only and does not call /api/analytics.json.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

BASE="${DHIS2_BASE_URL:-http://127.0.0.1:8080}"
AUTH="-u ${DHIS2_ADMIN_USER:-admin}:${DHIS2_ADMIN_PASSWORD:-district}"

fail=0
check() {
  local label="$1"; local path="$2"; local match_regex="${3:-}"
  local resp
  if ! resp=$(curl -fsS $AUTH "$BASE$path" 2>/dev/null); then
    echo "  FAIL: $label ($path)"; fail=1; return
  fi
  if [ -n "$match_regex" ] && ! echo "$resp" | grep -qE "$match_regex"; then
    echo "  FAIL: $label ($path): expected /$match_regex/"; fail=1; return
  fi
  echo "  PASS: $label"
}

echo "== 1/6 /api/system/info =="
check "system info" "/api/system/info" '"version"'

echo "== 2/6 /api/organisationUnits (paginated) =="
check "org units"        "/api/organisationUnits?pageSize=1&fields=id" '"total"'

echo "== 3/6 /api/dataElements =="
check "data elements"    "/api/dataElements?pageSize=1&fields=id"     '"total"'

echo "== 4/6 /api/dataElementGroups =="
check "data element grps" "/api/dataElementGroups?pageSize=1&fields=id" '"total"'

echo "== 5/6 /api/categoryCombos =="
check "category combos"  "/api/categoryCombos?pageSize=1&fields=id"   '"total"'

echo "== 6/6 /api/dataValueSets (raw, the ingestor's main extraction surface) =="
# Pull a small slice (last month, root org unit). Returns at least the JSON
# envelope even when no values match the window.
ROOT_OU=$(curl -fsS $AUTH "$BASE/api/organisationUnits?level=1&fields=id&paging=false" \
  | grep -oE '"id":"[^"]+"' | head -1 | cut -d'"' -f4)
check "dataValueSets" \
      "/api/dataValueSets?orgUnit=${ROOT_OU}&period=LAST_MONTH&dataSet=lyLU2wR22tC" \
      '"dataValues"|^\{'

echo ""
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "ONE OR MORE FAILED"; exit 1; fi
