#!/usr/bin/env bash
# Smoke-test the MFR seed. Prints counts per Organization type and a sample of
# health centres.
set -euo pipefail

BASE="${MFR_FHIR_BASE:-http://127.0.0.1:8093/fhir}"

echo "== total Organizations =="
curl -s "${BASE}/Organization?_summary=count" | python3 -c "import json,sys;print('  total:',json.load(sys.stdin).get('total'))"

echo
echo "== by type (region / zone / woreda / facility) =="
for level in country region zone woreda hp hc phc mc ph gh sh ph- ds; do
  n=$(curl -s "${BASE}/Organization?type=${level}&_summary=count" | python3 -c "import json,sys;print(json.load(sys.stdin).get('total',0))")
  printf "  %-8s %s\n" "$level" "$n"
done

echo
echo "== 5 random facilities =="
curl -s "${BASE}/Organization?type=hc&_count=5&_format=json" \
  | python3 -c "
import json,sys
b=json.load(sys.stdin)
for e in b.get('entry',[])[:5]:
    r=e['resource']; addr=(r.get('address') or [{}])[0]
    print('  ·',r['name'],'·',addr.get('district'),'/',addr.get('state'))
"
