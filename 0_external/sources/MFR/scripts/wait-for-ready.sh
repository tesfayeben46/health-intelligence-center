#!/usr/bin/env bash
# Block until the HAPI FHIR server reports a valid CapabilityStatement.
set -euo pipefail

BASE="${MFR_FHIR_BASE:-http://127.0.0.1:8093/fhir}"
RETRIES=60
SLEEP=2

echo "waiting for ${BASE}/metadata"
for i in $(seq 1 $RETRIES); do
  if curl -fs -o /dev/null "${BASE}/metadata"; then
    echo "ready after ${i} probes"
    exit 0
  fi
  printf '.'
  sleep "$SLEEP"
done
echo
echo "TIMED OUT after $((RETRIES * SLEEP))s" >&2
exit 1
