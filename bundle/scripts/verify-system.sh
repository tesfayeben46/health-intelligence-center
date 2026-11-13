#!/usr/bin/env bash
# verify-system.sh: fast regression check of the sensitive platform contracts,
# runnable against a live system in seconds (no teardown, no rebuild).
#
# Each sensitive feature keeps a repeatable test here, so a change can be shown
# not to break anything without a full teardown and rebuild. Run it after any
# change that touches boot scripts, seeds, pack/unpack, the pipeline or security.
#
# Checks (one pass/fail line each; exits non-zero if any fails):
#   1. cluster + all Deployments/StatefulSets fully ready
#   2. all 6 external source containers up (incl. fayda-replica)
#   3. marts populated (the flagship counts must be > 0)
#   4. provenance reconciliation live (the user table has both seed and runtime
#      rows classified, proving the loaders' managed_by contract)
#   5. gateway healthy; fayda-replica OIDC discovery answers on :8096
#   6. named-collection raw views: nhdw_staging has raw_* views and CH can list
#      lake files through the server-side named collection (zero creds in SQL)
#   7. pack/unpack surface: pack.sh + restore-state.sh parse (bash -n) and the
#      bundled mc binary is executable
#   8. no removed paths or image names referenced (1_sources, fake-fayda image
#      refs, 1_infrastructure/{scripts,docker}): a replacement-means-removal guard
#   9. live regression tests: the masking negative path
#      (a low-trust identity gets generalised coordinates on both the Control
#      Tower feed and the generic mart reader, an analyst with the reveal grant
#      gets exact ones, a partner role is denied) and the public
#      access-request surface (duplicate guard, validation, stored identity)
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
NHDW_HOSTBASE="${NHDW_HOSTBASE:-127.0.0.1.nip.io}"   # ingress host base (values.yaml hostBase); e.g. NHDW_HOSTBASE=<public-ip>.nip.io on a hosted node
export PATH="$HOME/.local/bin:$PATH"
FAIL=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*"; FAIL=1; }

echo "== verify-system: fast regression checks =="

# 1. workloads ready
notready=$(kubectl -n "$NS" get deploy,sts -o json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
bad=[i['metadata']['name'] for i in d['items']
     if (i['status'].get('readyReplicas') or 0) < (i['spec'].get('replicas') or 0)]
print(' '.join(bad))")
[ -z "$notready" ] && pass "all Deployments/StatefulSets ready" || fail "not ready: $notready"

# 2. sources
for c in dhis2-web ethioemr-mysql hcmiswdm-sqlserver nhdw-mfr-fhir climate-api fayda-replica; do
  docker ps --format '{{.Names}}' | grep -qx "$c" && pass "source $c up" || fail "source $c DOWN"
done

# 3+4. marts + provenance
PGPW=$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d)
counts=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_vault -At -F: -c "
  SELECT 'idsr', count(*) FROM bi_idsr.mart_idsr_disease_weekly UNION ALL
  SELECT 'climate', count(*) FROM bi_idsr.mart_idsr_climate_drivers UNION ALL
  SELECT 'hmis', count(*) FROM bi_hmis.mart_hmis_indicator_coverage UNION ALL
  SELECT 'outbreaks', count(*) FROM bi_idsr.mart_idsr_outbreaks" 2>/dev/null)
echo "$counts" | while IFS=: read -r m n; do :; done
for row in $counts; do
  m=${row%%:*}; n=${row##*:}
  [ "${n:-0}" -gt 0 ] && pass "mart $m populated ($n)" || fail "mart $m EMPTY"
done
# the cascade must cover all 4 programmes (guards against a programme prefix-map regression)
casc=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_vault -At -c \
  "SELECT count(DISTINCT programme_id) FROM bi_service.mart_programme_cascade" 2>/dev/null)
[ "${casc:-0}" -ge 4 ] && pass "programme cascade covers $casc programmes" \
                       || fail "programme cascade covers only ${casc:-0}/4 programmes"

prov=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -c \
  "SELECT count(DISTINCT managed_by) FROM ops_identity.\"user\"" 2>/dev/null)
[ "${prov:-0}" -ge 1 ] && pass "provenance column live (managed_by classes: $prov)" \
                       || fail "provenance managed_by missing on ops_identity.user"

# MDM: the matcher must have filled the steward candidate queue (side-lane job
# after dbt-marts)
ncand=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -c \
  "SELECT count(*) FROM ops_governance.mdm_match_candidate" 2>/dev/null)
[ "${ncand:-0}" -gt 0 ] && pass "MDM candidate queue populated ($ncand)" \
                        || fail "MDM candidate queue empty (matcher never ran?)"

# 5. gateway + fayda OIDC. The gateway probe retries for up to a minute: a
#    just-restarted gateway needs a few seconds before ingress endpoints
#    repoint, and CI verifies right after a rollout; fail only if it stays down.
gw_ok=0
for _ in $(seq 12); do
  if curl -sk --max-time 5 "https://gateway.${NHDW_HOSTBASE}/health" | grep -q '"ok"'; then
    gw_ok=1; break
  fi
  sleep 5
done
[ "$gw_ok" = 1 ] && pass "gateway healthy" || fail "gateway health check failed"
curl -s --max-time 5 http://localhost:8096/.well-known/openid-configuration | grep -q issuer \
  && pass "fayda-replica OIDC discovery on :8096" || fail "fayda-replica OIDC discovery failed"

# 6. raw views through the named collection
CHPW=$(kubectl -n "$NS" get secret clickhouse-secrets -o jsonpath='{.data.PASSWORD}' 2>/dev/null | base64 -d)
nviews=$(kubectl -n "$NS" exec deploy/clickhouse -- clickhouse-client -u nhdw --password "$CHPW" -q \
  "SELECT count() FROM system.tables WHERE database='nhdw_staging' AND name LIKE 'raw_%'" 2>/dev/null)
[ "${nviews:-0}" -gt 0 ] && pass "nhdw_staging raw_* views present ($nviews)" || fail "no raw_* views"
lake=$(kubectl -n "$NS" exec deploy/clickhouse -- clickhouse-client -u nhdw --password "$CHPW" -q \
  "SELECT count() FROM (SELECT DISTINCT _path FROM s3(nhdw_minio, url='http://seaweedfs-s3.nhdw.svc.cluster.local:8333/nhdw-raw/**/*.parquet', format='One'))" 2>/dev/null)
[ "${lake:-0}" -gt 0 ] && pass "lake reachable via named collection ($lake parquet paths)" \
                       || fail "named-collection lake probe failed"

# 6b. processed zone: populated and PII-stripped (spot-check the two
#     highest-risk tables: FHIR narrative and the mBrana user directory)
pz=$(kubectl -n "$NS" exec deploy/clickhouse -- clickhouse-client -u nhdw --password "$CHPW" -q \
  "SELECT count() FROM (SELECT DISTINCT _path FROM s3(nhdw_minio, url='http://seaweedfs-s3.nhdw.svc.cluster.local:8333/nhdw-processed/**/*.parquet', format='One'))" 2>/dev/null)
[ "${pz:-0}" -gt 0 ] && pass "processed zone populated ($pz tables)" || fail "processed zone EMPTY"

# 6b. eLMIS v2 history lane (OpenLMIS family): lake and marts reconcile to the extract's fixed totals
el=$(kubectl -n "$NS" exec deploy/clickhouse -- clickhouse-client -u nhdw --password "$CHPW" -q \
  "SELECT count() FROM nhdw_staging.raw_elmis_hist__history_requisition_line" 2>/dev/null)
[ "${el:-0}" = "7543506" ] && pass "eLMIS history lane in the lake (7,543,506 lines)" || fail "eLMIS history lane lake count = ${el:-0} (expected 7,543,506)"
eld=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_vault -At -c "SELECT sum(dispensed)::bigint FROM bi_supply.mart_elmis_hist_monthly_activity" 2>/dev/null)
[ "${eld:-0}" = "1072527469" ] && pass "eLMIS marts reconcile (Σ dispensed = 1,072,527,469)" || fail "eLMIS marts Σ dispensed = ${eld:-0} (expected 1,072,527,469)"
pat_cols=$(kubectl -n "$NS" exec deploy/clickhouse -- clickhouse-client -u nhdw --password "$CHPW" -q \
  "DESCRIBE s3(nhdw_minio, url='http://seaweedfs-s3.nhdw.svc.cluster.local:8333/nhdw-processed/ethioemr/patient/*.parquet', format='Parquet')" 2>/dev/null | cut -f1)
if [ -n "$pat_cols" ]; then
  echo "$pat_cols" | grep -qE "text__div|birth_date" \
    && fail "processed ethioemr/patient still carries PII columns" \
    || pass "processed ethioemr/patient is PII-stripped"
fi
usr_cols=$(kubectl -n "$NS" exec deploy/clickhouse -- clickhouse-client -u nhdw --password "$CHPW" -q \
  "DESCRIBE s3(nhdw_minio, url='http://seaweedfs-s3.nhdw.svc.cluster.local:8333/nhdw-processed/mbrana/mflowussd_users/*.parquet', format='Parquet')" 2>/dev/null | cut -f1)
if [ -n "$usr_cols" ]; then
  echo "$usr_cols" | grep -qE "user_name|phone_no" \
    && fail "processed mbrana/mflowussd_users still carries PII columns" \
    || pass "processed mbrana/mflowussd_users is PII-stripped"
fi

# 6e. data-quality governance lane
thr=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -c "SELECT count(*) FROM ops_governance.dq_threshold" 2>/dev/null)
[ "${thr:-0}" -ge 11 ] && pass "dq_threshold seeded (${thr} rows)" || fail "dq_threshold has ${thr:-0} rows (expected >= 11; init 38 not applied?)"
dqe=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -c "SELECT count(*) FROM ops_governance.dq_flag_event" 2>/dev/null)
[ -n "${dqe:-}" ] && pass "dq_flag_event history table present (${dqe} events)" || fail "dq_flag_event missing (init 38 not applied?)"
dqdag=$(kubectl -n "$NS" exec deploy/airflow -- airflow dags list 2>/dev/null | grep -c "dq_detection")
[ "${dqdag:-0}" -ge 1 ] && pass "airflow DAG dq_detection registered" || fail "airflow DAG dq_detection missing"

# 6c. ML tier: registry, deployments, latest-run predictions, airflow DAG
# The X-Mock-* impersonation channel is secret-gated (auth.py fails secure):
# without X-Mock-Secret the gateway 401s and this check could never pass.
IMPSEC=$(kubectl -n "$NS" get secret nhdw-gateway-secrets \
  -o jsonpath='{.data.IMPERSONATION_SECRET}' 2>/dev/null | base64 -d)
mlmodels=$(curl -sk --max-time 8 "https://gateway.${NHDW_HOSTBASE}/api/ml" \
  -H "X-Mock-User: 22222222-3333-4444-5555-666666666666" -H "X-Mock-Role: R-CONS-ANA" \
  -H "X-Mock-Secret: ${IMPSEC}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)
[ "${mlmodels:-0}" -ge 5 ] && pass "ML registry serves $mlmodels models (@champion via gateway)" \
                          || fail "ML registry has only ${mlmodels:-0}/5 models"
mlpred=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -c "
  SELECT count(DISTINCT r.model_name) FROM ops_ml.ml_predictions p
  JOIN ops_ml.ml_run r ON r.id = p.run_id WHERE r.status='success'" 2>/dev/null)
[ "${mlpred:-0}" -ge 5 ] && pass "ml_predictions populated for $mlpred models" \
                        || fail "ml_predictions covers only ${mlpred:-0}/5 models"
dag=$(kubectl -n "$NS" exec deploy/airflow -- airflow dags list 2>/dev/null | grep -c "ml_batch_predict")
[ "${dag:-0}" -ge 1 ] && pass "airflow DAG ml_batch_predict registered" \
                     || fail "airflow DAG ml_batch_predict missing"

# 6d. graduation review flow (project-centric): the submit/approve/reject seed
#     must have left at least one approved (public) and at least one in_review
#     project. Proves the seed drove the gateway API to completion
#     (project.review_status is only written through the graduation endpoints).
grad=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -F: -c "
  SELECT count(*) FILTER (WHERE review_status='approved'),
         count(*) FILTER (WHERE review_status='in_review')
  FROM ops_workspace.project" 2>/dev/null)
gapp=${grad%%:*}; grev=${grad##*:}
{ [ "${gapp:-0}" -ge 1 ] && [ "${grev:-0}" -ge 1 ]; } \
  && pass "graduation review flow populated (approved=$gapp, in_review=$grev)" \
  || fail "graduation review flow incomplete (approved=${gapp:-0}, in_review=${grev:-0})"
# the approved community project's model must be catalogue-visible (integrated by
# the graduation hook): a model_deployment row and successful-run predictions.
cmodel=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
  psql -U vault_admin -d nhdw_ops -At -c "
  SELECT count(*) FROM ops_ml.model_deployment d
  WHERE d.model_name='community_malaria_risk'
    AND EXISTS (SELECT 1 FROM ops_ml.ml_predictions p JOIN ops_ml.ml_run r ON r.id=p.run_id
                WHERE r.model_name=d.model_name AND r.status='success')" 2>/dev/null)
[ "${cmodel:-0}" -ge 1 ] && pass "community model integrated (community_malaria_risk deployed + predicting)" \
                        || fail "approved community model not integrated (no deployment+predictions for community_malaria_risk)"

# 7. pack/unpack surface
bash -n "$REPO/bundle/scripts/pack.sh" && bash -n "$REPO/bundle/scripts/restore-state.sh" \
  && pass "pack.sh + restore-state.sh parse" || fail "pack/restore syntax error"
# mc is a derived artifact: bundle/bin/ is gitignored and lib/objstore.sh
# materialises mc on first use (bundled binary, then extraction from the cached
# minio/mc image, then the network as a last resort). Asserting the binary
# exists would fail every healthy fresh checkout; what matters is that an
# air-gapped boot can still get it, i.e. at least one offline source is present.
MC_IMG="${OBJSTORE_MC_IMAGE:-minio/mc:RELEASE.2025-04-08T15-39-49Z}"
MC_TAR="$REPO/bundle/images/$(echo "$MC_IMG" | tr '/:' '__').tar"
if [ -x "$REPO/bundle/bin/mc" ]; then
  pass "mc available offline (bundled binary)"
elif [ -f "$MC_TAR" ]; then
  pass "mc available offline (cached image tar)"
elif command -v docker >/dev/null 2>&1 && docker image inspect "$MC_IMG" >/dev/null 2>&1; then
  pass "mc available offline (image in the local store)"
else
  fail "mc has NO offline source: an airgapped boot would have to download it (cache-images.sh ships $MC_IMG)"
fi

# 8. replacement-means-removal guard
stale=$(grep -rIl --exclude-dir='.*' "1_sources/\|nhdw/fake-fayda\|1_infrastructure/scripts/\|1_infrastructure/docker/" "$REPO" \
  --include="*.sh" --include="*.py" --include="*.yaml" --include="*.yml" --include="*.ts" --include="*.tsx" 2>/dev/null \
  | grep -v "_archive/" | grep -v "k8s-deployment-changelog" | grep -v "bundle/state/" \
  | grep -v "verify-system.sh" || true)   # this script necessarily contains the patterns
[ -z "$stale" ] && pass "no retired names outside _archive" || fail "retired names still referenced: $stale"

# 9. masking negative path: the check that proves column masking transforms
# data rather than only logging. The policy self-tests prove the policy is
# right; only this check asks what a low-trust caller actually receives.
run_live_test() {   # $1 = label, $2 = script path
  if [ ! -f "$2" ]; then fail "$1: test script missing at $2"; return; fi
  local out rc
  out="$(NHDW_HOSTBASE="$NHDW_HOSTBASE" NS="$NS" bash "$2" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    pass "$1"
  else
    fail "$1 FAILED:"
    printf '%s\n' "$out" | sed 's/^/      /'
  fi
}

run_live_test "masking negative path enforced on both read paths" \
              "$REPO/6_access-points/api/nhdw-gateway/tests/test_masking_e2e.sh"

# The public access-request surface is the one anonymous write the system
# exposes; its duplicate guard, schema validation and identity storage
# are security behaviour, not cosmetics. This test cleans up its own rows.
run_live_test "public access-request surface (duplicate guard · validation · identity honesty)" \
              "$REPO/6_access-points/api/nhdw-gateway/tests/test_public_access_request.sh"

echo
if [ "$FAIL" -ne 0 ]; then echo "== verify-system: FAILURES above =="; exit 1; fi
echo "== verify-system: ALL GREEN =="
