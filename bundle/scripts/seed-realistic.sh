#!/usr/bin/env bash
# seed-realistic.sh: seeds the data-pipeline tiers.
#
# Pulls the data from the external source replicas through the dlt ingestors,
# then runs the dbt pipeline end to end, so the marts are produced by the
# production transformations:
#
#   seaweedfs-init -> [7 source ingestors, parallel] -> ch-raw-views -> processed-zone
#              -> dbt-staging -> bridge-sync -> dbt-vault -> dbt-marts -> (component views)
#
# The source replicas must already be up (bundle/scripts/sources-up.sh, run by
# bootstrap); a preflight probes host.k3d.internal:<port> for every source from
# inside the cluster and fails if one is unreachable.
#
# Each step is a K8s Job applied and waited on in dependency order. The registry
# image names in base/dbt-jobs.yaml and base/ingestor-jobs.yaml are remapped to
# the locally built nhdw/*:dev images, and imagePullPolicy is IfNotPresent
# (images are pre-imported into the cluster; see cache-images.sh, build-images.sh).
#
# Usage:  bundle/scripts/seed-realistic.sh [--namespace=nhdw]
#           [--skip-ingest]    reuse the current lake; run only the dbt pipeline
#           [--full-refresh]   purge every source's lake prefix (data + dlt state,
#                              so incremental cursors restart) before ingesting:
#                              a clean, duplicate-free rebuild
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw

# A per-run private workdir for rendered Job manifests. A fixed /tmp path is
# owned by whoever ran first, so another user's run (e.g. a different CI actor)
# gets 'Permission denied'. mktemp gives each run its own directory; the trap
# removes it however the script exits.
JOBTMP="$(mktemp -d "${TMPDIR:-/tmp}/nhdw-seed.XXXXXX")"
trap 'rm -rf "$JOBTMP"' EXIT
INGEST=1
FULL_REFRESH=0
for a in "$@"; do case "$a" in
  --namespace=*) NS="${a#--namespace=}";;
  --skip-ingest) INGEST=0;;
  --full-refresh) FULL_REFRESH=1;;
esac; done

# Ingestors run in parallel (independent sources write disjoint lake prefixes;
# hcmiswdm and mbrana share one SQL Server but only read). dbt stages are strictly ordered.
INGEST_JOBS=(dhis2-ingest-metadata dhis2-ingest-dvs climate-ingest ethioemr-ingest hcmiswdm-ingest mbrana-ingest mfr-ingest elmis-hist-ingest)   # elmis-hist: OpenLMIS historical extract (the live openlmis-ingest lane is not scheduled)

# Sources the active profile does not provide are dropped from every stage that
# references them (the ingestor list here and the preflight probe below), using
# one skip set so they cannot disagree. climate has no non-Ethiopian data, so a
# non-default profile skips it unless NHDW_SKIP_SOURCES says otherwise.
SKIP_SRC="${NHDW_SKIP_SOURCES:-}"
if [ -z "$SKIP_SRC" ] && [ "${NHDW_PROFILE:-et-v3}" != "et-v3" ]; then SKIP_SRC="climate hcmiswdm mbrana"; fi
if [ -n "$SKIP_SRC" ]; then
  _keep=(); for j in "${INGEST_JOBS[@]}"; do
    _drop=0; for s in $SKIP_SRC; do case "$j" in ${s}-ingest*) _drop=1;; esac; done
    [ "$_drop" = 0 ] && _keep+=("$j")
  done; INGEST_JOBS=("${_keep[@]}")
  echo "▸ profile ${NHDW_PROFILE:-et-v3}: skipping source(s) [$SKIP_SRC]; ${#INGEST_JOBS[@]} ingestors remain"
fi
# mdm-matcher rides after dbt-marts: it reads the freshly built resolution view
# and (re)fills the steward candidate queue; a side lane that never blocks the marts.
PIPELINE=(ch-raw-views processed-zone dbt-staging bridge-sync dbt-vault dbt-marts mdm-matcher publish-datasets)

# Registry-to-local image remap uses kustomize's built-in images transform
# (1_infrastructure/k8s/jobs/kustomization.yaml); orchestration never rewrites
# manifests.
CORRECTED="$(kustomize build --load-restrictor=LoadRestrictionsNone "$REPO/1_infrastructure/k8s/jobs")"

extract_job() { # job-name -> writes $JOBTMP/job-<name>.yaml
  # Jobs come from the k8s/jobs build (dbt + ingestor Jobs, images remapped). The
  # object-store init Job (seaweedfs-init) lives with its stack in base/seaweedfs.yaml
  # (rendered from the chart); read it from there so it is never duplicated.
  { printf '%s\n---\n' "$CORRECTED"; cat "$REPO/1_infrastructure/k8s/base/seaweedfs.yaml"; } | python3 -c "
import sys,yaml
docs=[d for d in yaml.safe_load_all(sys.stdin) if isinstance(d,dict)]
j=[d for d in docs if d.get('kind')=='Job' and d['metadata']['name']=='$1']
if not j: sys.exit('  no Job named $1 in dbt-jobs.yaml/ingestor-jobs.yaml/seaweedfs.yaml')
print(yaml.safe_dump(j[0]))
" > "$JOBTMP/job-$1.yaml"
}

# Wait for a Job to finish: return 0 on Complete, 1 on Failed or timeout.
# (A bare `kubectl wait --for=condition=complete` blocks the full timeout on a job
# that has already failed; this polls both terminal conditions and fails fast.)
wait_job() { # job-name timeout-seconds
  local job="$1" timeout="${2:-420}" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    local st
    st=$(kubectl -n "$NS" get job "$job" \
      -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}' 2>/dev/null)
    case "$st" in
      *Complete=True*) return 0 ;;
      *Failed=True*)   return 1 ;;
    esac
    # A just-launched job that is now absent was TTL-collected after finishing
    # while a slower sibling was being waited on (ttlSecondsAfterFinished). Do
    # not poll a missing job for the whole budget: return non-zero so the solo
    # retry re-applies it and confirms it in seconds. (With the 24h ingestor
    # TTL this is not expected to fire.)
    if ! kubectl -n "$NS" get job "$job" >/dev/null 2>&1; then return 1; fi
    sleep 5; waited=$((waited+5))
  done
  return 1
}

run_job() {
  local job="$1" timeout="${2:-420}"
  echo "▸ [$job]"
  extract_job "$job" || return 1
  kubectl -n "$NS" delete job "$job" --ignore-not-found --wait=true >/dev/null 2>&1
  kubectl -n "$NS" apply -f "$JOBTMP/job-$job.yaml" >/dev/null || return 1
  if wait_job "$job" "$timeout"; then
    echo "  ok: $job complete"
    return 0
  fi
  echo "  FAIL: $job did not complete: logs:"
  kubectl -n "$NS" logs "job/$job" --tail=25 2>/dev/null | sed 's/^/    /'
  return 1
}

echo "== REALISTIC seeding (source replicas -> ingestors -> lake -> dbt pipeline -> marts) =="
command -v kustomize >/dev/null || export PATH="$HOME/.local/bin:$PATH"

# stage 1: the lake buckets must exist before the ingestors write to them
# The lake lives on SeaweedFS; seaweedfs-init creates the buckets and lifecycle
# rules there (idempotent).
run_job seaweedfs-init || { echo "FAILED at seaweedfs-init"; exit 1; }

# stage 2: source -> lake via the dlt ingestors
if [ "$INGEST" = 1 ]; then
  # Preflight: every ingestor reaches its source over host.k3d.internal:<published
  # port>. Probe from inside the cluster (an imported ingestor image has python3),
  # so a broken host route fails here instead of producing 0-row marts later.
  echo "▸ [preflight] source reachability from inside the cluster (host.k3d.internal)"
    # Sources are host containers slow to accept connections: a fresh DHIS2 or
    # SQL Server is not listening the instant docker reports up, and a country
    # switch that just recreated them widens that window. Retry with backoff.
    [ -n "$SKIP_SRC" ] && echo "  (preflight skips: $SKIP_SRC)"
    PF_TRIES="${NHDW_PREFLIGHT_TRIES:-20}"; pf_ok=0
    for pf_i in $(seq 1 "$PF_TRIES"); do
  if kubectl -n "$NS" run src-preflight --rm -i --restart=Never \
      --image=nhdw/dhis2-ingestor:1.0.0 --image-pull-policy=IfNotPresent --quiet \
      --env="PF_SKIP=$SKIP_SRC" \
      --command -- python3 -c '
import socket, sys
ports = {"dhis2": 8080, "climate": 8095, "mfr": 8093,
         "sqlserver(hcmiswdm+mbrana)": 1433, "ethioemr-mysql": 33306, "elmis-hist-postgres": 5434}
import os as _os
ports = {n: p for n, p in ports.items() if not any(k in n for k in _os.environ.get("PF_SKIP","").split())}
bad = []
for name, p in ports.items():
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect(("host.k3d.internal", p)); print(f"    ok   {name} :{p}")
    except Exception as exc:
        print(f"    FAIL {name} :{p} ({exc})"); bad.append(name)
    finally:
        s.close()
sys.exit(1 if bad else 0)' 2>/dev/null; then
      pf_ok=1; echo "  ok: all sources reachable (attempt ${pf_i})"; break
    fi
    echo "    sources not all ready (attempt ${pf_i}/${PF_TRIES}): waiting 15s"; sleep 15
    done
    if [ "$pf_ok" != 1 ]; then echo "  FAIL: sources still unreachable after ${PF_TRIES} attempts"; exit 1; fi

  # Optional clean rebuild: purge each source's whole lake prefix, data and _dlt
  # state, so append and incremental cursors restart from the beginning and
  # nothing can double-count. (A normal run without this flag is incremental:
  # append sources pull only their delta; replace sources rewrite their prefix.)
  if [ "$FULL_REFRESH" = 1 ]; then
    echo "▸ [ingest] FULL REFRESH: purging lake prefixes (data + dlt state)"
    . "$REPO/bundle/scripts/lib/objstore.sh"
    objstore_mc && objstore_creds && objstore_pf 19004 && objstore_alias || { echo "  FAIL: cannot reach the object store for the purge"; exit 1; }
    for src in dhis2 climate ethioemr hcmiswdm mbrana mfr elmis_hist; do
      "$MC" --config-dir "$OS_MC_CFG" rm --recursive --force "L/nhdw-raw/$src/" >/dev/null 2>&1; true
      echo "  purged nhdw-raw/$src/"
    done
    objstore_pf_stop
  fi

  # Bounded-concurrency launch (INGEST_BATCH, default 3): run the memory-heavy
  # ingestors a few at a time instead of all at once, so the parallel fan-out
  # cannot OOM-kill one (e.g. the EthioEMR observation scan) on a host with
  # little RAM. Wall clock is roughly the sum of each batch's slowest source; the
  # serial-retry pass below still catches stragglers. INGEST_BATCH=99 runs them
  # all at once on a host with ample RAM.
  BATCH="${INGEST_BATCH:-3}"
  # Per-ingestor completion budget. The 25 min default suits a laptop; a k3s host
  # reaches DHIS2 over the pod -> node -> host-docker hop, which is slower than the
  # k3d route, so the large data-value-sets extract can exceed it (raise INGEST_WAIT).
  INGEST_WAIT="${INGEST_WAIT:-1500}"
  echo "▸ [ingest] launching ${#INGEST_JOBS[@]} source ingestors, ${BATCH} at a time (per-job budget ${INGEST_WAIT}s)"
  retry=()
  i=0
  while [ "$i" -lt "${#INGEST_JOBS[@]}" ]; do
    batch=("${INGEST_JOBS[@]:i:BATCH}")
    for job in "${batch[@]}"; do
      extract_job "$job" || { echo "FAILED extracting $job"; exit 1; }
      kubectl -n "$NS" delete job "$job" --ignore-not-found --wait=true >/dev/null 2>&1
      kubectl -n "$NS" apply -f "$JOBTMP/job-$job.yaml" >/dev/null || { echo "FAILED applying $job"; exit 1; }
    done
    echo "  ▸ batch launched: ${batch[*]}"
    for job in "${batch[@]}"; do
      if wait_job "$job" "$INGEST_WAIT"; then
        echo "  ok: $job"
      else
        echo "  ! $job did not complete in its batch, queuing solo retry"
        retry+=("$job")
      fi
    done
    i=$((i + BATCH))
  done
  # Serial-retry pass: parallel memory-heavy ingestors can OOM-kill one (e.g. the
  # EthioEMR observation scan) on a RAM-constrained host, while the same ingestor
  # succeeds quickly when run alone. Re-run each failed ingestor one at a time
  # before failing the stage.
  if [ "${#retry[@]}" -gt 0 ]; then
    echo "▸ [ingest] serial-retry pass for ${#retry[@]} ingestor(s) that lost the parallel race: ${retry[*]}"
    ingest_failed=0
    for job in "${retry[@]}"; do
      kubectl -n "$NS" delete job "$job" --ignore-not-found --wait=true >/dev/null 2>&1
      kubectl -n "$NS" apply -f "$JOBTMP/job-$job.yaml" >/dev/null || { echo "FAILED re-applying $job"; exit 1; }
      if wait_job "$job" "$INGEST_WAIT"; then
        echo "  ok: $job (solo retry)"
      else
        echo "  FAIL: $job FAILED even on a solo retry, logs:"
        kubectl -n "$NS" logs "job/$job" --tail=20 2>/dev/null | sed 's/^/    /'
        ingest_failed=1
      fi
    done
    [ "$ingest_failed" = 0 ] || { echo "FAILED at ingest stage (after solo retry)"; exit 1; }
  fi
else
  echo "▸ [ingest] SKIPPED (--skip-ingest): reusing the current lake"
fi

# stage 3: MFR landing (the one explicitly typed, direct-loaded source)
# Other preparation lives with the components that own it:
#   · resilient raw views, stale-view drop and lake probe: apply_raw_views.py
#     inside the ch-raw-views Job (credentials via the server-side nhdw_minio
#     named collection, a historical name for the S3 object store; nothing is
#     injected from here);
#   · nhdw_landing reset: the bridge itself (DROP + CREATE per sync; it owns
#     its tables).
# MFR is not a bridge-managed source: its landing tables carry an explicit,
# typed schema (boolean, double, numeric, JSON-as-text columns from FHIR) that
# the all-string lake -> ClickHouse -> bridge path cannot reproduce. MFR therefore
# lands directly from its seed CSVs (a \copy dump of these same tables).
# Recreate the schema (the reset above dropped it) and load the CSVs.
PGADMIN_PW=$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
echo "▸ [prep] restoring MFR landing (explicit typed schema + direct CSV load)"
kubectl -n "$NS" exec postgres-vault-0 -i -- \
  env PGPASSWORD="$PGADMIN_PW" psql -U vault_admin -d nhdw_vault -v ON_ERROR_STOP=1 -q \
  < "$REPO/3_storage/postgres/init/10_mfr_landing.sql" >/dev/null 2>&1 \
  && echo "  ok: MFR landing schema recreated" || echo "  ! MFR schema recreate failed"
mfr_ok=1
for t in organizations facilities devices; do
  csv="$REPO/bundle/seeds/sources/mfr/${t}.csv"
  [ -f "$csv" ] || { echo "  ! missing $csv"; mfr_ok=0; continue; }
  kubectl -n "$NS" exec postgres-vault-0 -i -- \
    env PGPASSWORD="$PGADMIN_PW" psql -U vault_admin -d nhdw_vault -v ON_ERROR_STOP=1 -q \
    -c "TRUNCATE nhdw_landing.stg_mfr__${t}" \
    -c "\copy nhdw_landing.stg_mfr__${t} FROM STDIN WITH (FORMAT csv, HEADER true)" \
    < "$csv" >/dev/null 2>&1 || { echo "  ! MFR load failed: $t"; mfr_ok=0; }
done
[ "$mfr_ok" = 1 ] && echo "  ok: MFR landing loaded (organizations + facilities + devices)"

# IDSR and Climate are not direct-loaded here. IDSR is modelled as DHIS2 data
# (disease data elements + weekly data values in the dhis2 seed) and Climate is a
# bridge-managed source (lake -> ClickHouse -> bridge). Both use the standard
# pipeline (ingestors -> ch-raw-views -> dbt-staging -> bridge-sync -> dbt-vault
# -> dbt-marts) and reach the marts via nhdw_vault_raw.

# Drop stale raw_*/stg_* views before the pipeline recreates the current set.
# Neither `CREATE OR REPLACE` (ch-raw-views) nor dbt drops a view whose model was
# removed from the project, so a removed source leaves a raw_/stg_ view in
# nhdw_staging pointing at an empty s3 path. The bridge reads every stg_* view
# and aborts with `CANNOT_EXTRACT_TABLE_STRUCTURE` (636). nhdw_staging holds only
# raw_/stg_ views and no data, so rebuilding it each run is safe.
if [ -n "${CHPOD:-}" ]; then
  echo "▸ [prep] dropping stale raw_*/stg_* views (removed models must not linger)"
  DROPS=$(kubectl -n "$NS" exec "$CHPOD" -- clickhouse-client -q \
    "SELECT 'DROP VIEW IF EXISTS nhdw_staging.\`' || name || '\`;' FROM system.tables \
     WHERE database='nhdw_staging' AND (name LIKE 'raw_%' OR name LIKE 'stg_%')" 2>/dev/null)
  if [ -n "$DROPS" ]; then
    printf '%s\n' "$DROPS" | kubectl -n "$NS" exec -i "$CHPOD" -- clickhouse-client -n >/dev/null 2>&1 \
      && echo "  ok: stale views dropped ($(printf '%s\n' "$DROPS" | grep -c DROP))" \
      || echo "  ! stale-view drop skipped"
  else
    echo "  ok: no stale views"
  fi
fi

# stage 4: the dbt pipeline (strictly ordered)
# The dbt build stages need a long wait: a cold boot builds them with
# --full-refresh (see each Job's cold/warm FLAG logic), and a full refresh of the
# vault and all marts (including mart_hmis_indicator_coverage, which is heavy on
# the business-vault spine) takes 10 to 16 minutes. The default 420s wait_job
# timeout would abort a stage that is still running successfully.
for job in "${PIPELINE[@]}"; do
  case "$job" in
    dbt-vault|dbt-marts) to=2400 ;;   # cold full-refresh: up to 40 min (marts can be
                                      # slow under concurrent Job contention)
    *)                   to=420  ;;
  esac
  run_job "$job" "$to" || { echo "FAILED at $job"; exit 1; }
done

echo "== realistic seeding complete =="
echo "Mart row counts:"
kubectl -n "$NS" exec postgres-vault-0 -- \
  env PGPASSWORD="$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)" \
  psql -U vault_admin -d nhdw_vault -At -c \
  "SELECT schemaname||'.'||relname||'='||n_live_tup FROM pg_stat_user_tables WHERE schemaname LIKE 'bi_%' AND n_live_tup>0 ORDER BY 1;" 2>/dev/null | sed 's/^/  /'
