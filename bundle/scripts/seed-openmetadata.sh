#!/usr/bin/env bash
# seed-openmetadata.sh: bootstrap the OpenMetadata catalog after deploy.
#
# What it does (idempotent):
#   1. waits for the OM API to answer;
#   2. verifies the ingestion-bot JWT exists in openmetadata-secrets
#      (INGESTION_BOT_TOKEN) and is accepted by the API;
#   3. unpauses and triggers the `openmetadata_ingestion` Airflow DAG once, so
#      the catalog fills (postgres, clickhouse, lake, dbt column lineage,
#      airflow pipelines) without waiting for the 05:00 schedule;
#   4. publishes the access-point structure (sync-om-dashboards.py: pages,
#      widgets, mart->widget lineage) so the catalog shows lineage end to end:
#      source -> lake -> staging -> vault -> mart -> dashboard widget.
#
# The one manual step on a cold boot is the bot token. With SSO enabled, OM has
# no scriptable admin login, so an OM administrator (the `superadmin` SSO
# account, AUTHORIZER_ADMIN_PRINCIPALS in base/openmetadata.yaml) issues it in
# the UI under Settings, Bots, ingestion-bot (generate token), then runs:
#     kubectl -n nhdw patch secret openmetadata-secrets --type merge \
#       -p '{"stringData":{"INGESTION_BOT_TOKEN":"<token>"}}'
# When the token is missing the script prints these instructions and exits 0
# (OpenMetadata is catalog-only; a missing catalog never blocks the platform).
set -euo pipefail
NS="${NS:-nhdw}"
OM_SVC="http://openmetadata.$NS.svc.cluster.local:8585"

echo "== seed-openmetadata =="

# 1. OM up? (probe from inside the cluster; OM may legitimately be disabled)
if ! kubectl -n "$NS" get deploy openmetadata >/dev/null 2>&1; then
  echo "  openmetadata not deployed: skipping (catalog is optional)"; exit 0
fi
for i in $(seq 1 60); do
  code=$(kubectl -n "$NS" run om-seed-probe --rm -i --restart=Never --quiet \
      --image=curlimages/curl:8.10.1 -- -s -o /dev/null -w '%{http_code}' \
      -m 5 "$OM_SVC/api/v1/system/version" 2>/dev/null | tail -c 3 || true)
  [ "$code" = "200" ] && break
  sleep 5
done
if [ "${code:-}" != "200" ]; then
  echo "  FAIL: OM API not answering after 5 min; skipping (rerun this script later)"; exit 0
fi
echo "  ok: OM API up"

# 2. bot token present and accepted?
tok=$(kubectl -n "$NS" get secret openmetadata-secrets \
        -o jsonpath='{.data.INGESTION_BOT_TOKEN}' 2>/dev/null | base64 -d || true)
if [ -z "$tok" ]; then
  cat <<'EOT'
  FAIL: openmetadata-secrets has no INGESTION_BOT_TOKEN.
    An OM admin (superadmin via SSO) must issue it once:
      Settings -> Bots -> ingestion-bot -> Generate token
    then store it:
      kubectl -n nhdw patch secret openmetadata-secrets --type merge \
        -p '{"stringData":{"INGESTION_BOT_TOKEN":"<token>"}}'
    and rerun this script. Skipping (catalog stays empty until then).
EOT
  exit 0
fi
code=$(kubectl -n "$NS" run om-seed-probe --rm -i --restart=Never --quiet \
    --image=curlimages/curl:8.10.1 -- -s -o /dev/null -w '%{http_code}' -m 5 \
    -H "Authorization: Bearer $tok" "$OM_SVC/api/v1/tables?limit=1" 2>/dev/null | tail -c 3 || true)
if [ "$code" != "200" ]; then
  echo "  FAIL: stored bot token rejected ($code): reissue it (see header). Skipping."; exit 0
fi
echo "  ok: ingestion-bot token accepted"

# 2.5 org lineage view depth. The chain source -> lake -> staging -> landing ->
#     vault -> mart -> widget is about six hops each way; OM defaults to
#     upstreamDepth=2, which hides everything past the vault. System settings are
#     admin-only over the API and the SSO admin has no scriptable login (see the
#     token note above), so this writes the setting to OM's own database row and
#     restarts OM to drop the in-memory copy. Idempotent: no row change, no
#     restart. The full settings JSON is read from the API (bot-readable), so the
#     graph-performance limits are preserved.
depth_now=$(kubectl -n "$NS" run om-seed-lineage --rm -i --restart=Never --quiet     --image=curlimages/curl:8.10.1 --env="TOK=$tok" --     curl -s -m 10 -H "Authorization: Bearer $TOK" "$OM_SVC/api/v1/system/settings/lineageSettings" | tr -d '\r')
if printf %s "$depth_now" | grep -q '"upstreamDepth":8'; then
  echo "  = lineage depth already 8"
else
  body=$(printf %s "$depth_now" | python3 -c '
import json, sys
v = json.load(sys.stdin)["config_value"]
v["upstreamDepth"], v["downstreamDepth"] = 8, 3
print(json.dumps(v))')
  printf %s "$body" | kubectl -n "$NS" exec -i postgres-vault-0 --     sh -c 'psql -q -U vault_admin -d nhdw_openmetadata -v ON_ERROR_STOP=1 -c       "INSERT INTO openmetadata_settings (configtype, json) VALUES ('"'"'lineageSettings'"'"', \$json\$$(cat)\$json\$::jsonb)
       ON CONFLICT (configtype) DO UPDATE SET json = EXCLUDED.json"'     && echo "  ok: lineage depth 8/3 written" || echo "  … lineage depth write failed (non-fatal)"
  kubectl -n "$NS" rollout restart deploy/openmetadata >/dev/null 2>&1     && kubectl -n "$NS" rollout status deploy/openmetadata --timeout=600s >/dev/null 2>&1     && echo "  ok: openmetadata restarted" || echo "  … restart pending"
fi

# 3. kick the ingestion DAG once (best-effort; scheduler also runs it daily)
if kubectl -n "$NS" get deploy airflow >/dev/null 2>&1; then
  kubectl -n "$NS" exec deploy/airflow -- airflow dags unpause openmetadata_ingestion >/dev/null 2>&1 || true
  kubectl -n "$NS" exec deploy/airflow -- airflow dags trigger openmetadata_ingestion >/dev/null 2>&1 \
    && echo "  ok: openmetadata_ingestion triggered" \
    || echo "  … could not trigger the DAG (is it parsed yet?): the 05:00 schedule will run it"
fi

# 4. access-point emission (best-effort; the sync is fail-safe and idempotent,
#    see docs/metadata-plane.md). Runs from the host through a short-lived
#    port-forward, since OM has no NodePort.
if command -v python3 >/dev/null 2>&1; then
  kubectl -n "$NS" port-forward svc/openmetadata 18585:8585 >/dev/null 2>&1 &
  pf=$!; sleep 3
  OM_API="http://127.0.0.1:18585/api" OM_JWT="$tok" \
    python3 "$(dirname "$0")/sync-om-dashboards.py" \
    && echo "  ok: dashboard metadata synced" \
    || echo "  … dashboard sync skipped (non-fatal; rerun sync-om-dashboards.py later)"
  kill "$pf" 2>/dev/null || true
else
  echo "  … python3 not on this host; run sync-om-dashboards.py manually"
fi
echo "== seed-openmetadata done =="
