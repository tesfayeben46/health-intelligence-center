#!/usr/bin/env bash
# ci-redeploy.sh: push-driven redeploy for the live k3s cluster (main and develop).
#
# Given a set of changed files (git diff of the push, or "all"), it deploys only
# what changed:
#   * app tier:  rebuild the affected SPA/gateway images, import into k3s,
#                restart the workloads; reseed catalog/RBAC when those changed.
#   * data tier: apply new or changed Postgres init DDL (idempotent by contract),
#                reload the nhdw_ref vocabulary when its seeds changed, rebuild
#                the affected dbt/bridge images, and rerun the dbt job chain
#                from the earliest affected stage through dbt-marts (a vault
#                run drops the report views until marts rebuilds them, so the
#                chain never ends early).
# Then runs the system verifier. This is the deterministic core the GitHub
# Actions `deploy.yml` calls on a self-hosted runner on the k3s host (so it has
# docker, kubectl and the repo). Safe to run by hand:
#   bundle/scripts/ci-redeploy.sh all
#
# It is idempotent and additive: it never provisions or wipes. Provisioning
# from bare metal is the ansible `site.yml` (workflow `provision.yml`).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS="${NS:-nhdw}"
DASH_CTX="$REPO/6_access-points/dashboards"
export PATH="$HOME/.local/bin:$PATH"   # kustomize on the runner

# Per-run private scratch dir. Fixed /tmp names are owned by whoever ran first on
# a shared machine, and a sticky /tmp then refuses the second user's write,
# which makes successful builds report as failed. $RUNTMP is removed on exit.
RUNTMP="$(mktemp -d "${TMPDIR:-/tmp}/nhdw-ci-XXXXXX")"
trap 'rm -rf "$RUNTMP"' EXIT

# component map:  <path-prefix> | <image> | <dockerfile (rel to context)> | <context> | <deploy>
COMPONENTS=(
  "6_access-points/dashboards/internal-bi|nhdw/internal-bi:1.0.0|internal-bi/Dockerfile|$DASH_CTX|internal-bi"
  "6_access-points/dashboards/public|nhdw/public-dashboard:1.0.0|Dockerfile|$DASH_CTX/public|public-dashboard"
  "6_access-points/dashboards/admin|nhdw/admin:1.0.0|admin/Dockerfile|$DASH_CTX|admin"
  "6_access-points/dashboards/workshop|nhdw/workshop:1.0.0|workshop/Dockerfile|$DASH_CTX|workshop"
  "6_access-points/api/nhdw-gateway|nhdw/gateway:1.0.0|Dockerfile|$REPO/6_access-points/api/nhdw-gateway|nhdw-gateway"
  # DAGs are baked into the airflow image (5_orchestrator/Dockerfile); a DAG
  # change must rebuild and restart it or the scheduler keeps the old code
  "5_orchestrator|nhdw/airflow:1.0.0|Dockerfile|$REPO/5_orchestrator|airflow"
)
# paths whose change triggers a catalog/RBAC reseed (no image rebuild)
RESEED_GLOBS=("6_access-points/dashboards/internal-bi/catalog" "bundle/seed-pack/app/security" "6_access-points/dashboards/public/static/articles" "7_security/scripts/" "bundle/roles/")

changed_input="${1:-all}"
if [ "$changed_input" = "all" ]; then
  CHANGED="$(git -C "$REPO" ls-files)"
else
  # One path per line, whatever the caller sent (deploy.yml passes a space-
  # separated string): the DDL loop and the bridge/vault disambiguation use
  # line-anchored greps, which match nothing against a single line.
  CHANGED="$(printf '%s\n' $changed_input)"
fi

log(){ echo "▸ $*"; }
touched(){ echo "$CHANGED" | grep -q -- "$1"; }

# data tier, part 1: schema and vocabulary first
# DDL and the vocabulary reload run before the app tier: the catalog/RBAC
# reseed loads column_classification.yaml (and other seeds) into tables this
# DDL may have just created, so the order matters.

# 1. New or changed Postgres init DDL. The .sql files are idempotent by repo
#    contract (IF NOT EXISTS everywhere), and Postgres only runs init on a
#    fresh volume, so CI applies changed ones to the live DB. (.sh init files
#    create databases and roles from env and stay boot-only: flagged, not run.)
DDL_APPLIED=(); DDL_VAULT=()
for f in $(echo "$CHANGED" | grep -E '^3_storage/postgres/init/.*\.sql$' | sort); do
  [ -f "$REPO/$f" ] || continue     # deleted file
  log "apply init DDL $f (idempotent)"
  if kubectl -n "$NS" exec -i postgres-vault-0 -- psql -q -U vault_admin -d nhdw_vault -v ON_ERROR_STOP=1 < "$REPO/$f" >"$RUNTMP"/ci-ddl.log 2>&1; then
    DDL_APPLIED+=("$f")
    # ops-side files (\connect nhdw_ops) do not touch what dbt reads; only
    # vault-side DDL needs the vault -> marts job cascade
    grep -q '^\\connect nhdw_ops' "$REPO/$f" || DDL_VAULT+=("$f")
  else
    echo "  FAIL: DDL apply failed for $f"; tail -10 "$RUNTMP"/ci-ddl.log; exit 1
  fi
done
echo "$CHANGED" | grep -qE '^3_storage/postgres/init/.*\.sh$' \
  && log "changed init .sh (db/role bootstrap): boot-only, apply by hand if a new database is needed"

# 2. Reference vocabulary reload when its seeds changed (idempotent upsert).
if touched "bundle/seeds/ref-vocab/"; then
  log "reload nhdw_ref vocabulary"
  bash "$REPO/bundle/scripts/seed-ref-vocab.sh" --no-build 2>&1 | tail -3 || { echo "  FAIL: vocab reload failed"; exit 1; }
fi

# 2b. Country profiles to the hostPath the gateway mounts read-only.
#     The gateway reads /opt/nhdw-profiles (hostPath, DirectoryOrCreate; see
#     nhdw-gateway.yaml) because the geometry is too large for a configMap and
#     a profile swap has to be a file write the gateway sees without a rollout.
#     This step is what ships a committed profile edit to that directory. The
#     ACTIVE pointer is state, lives in a different directory (/opt/nhdw-state),
#     and is left untouched.
if touched "bundle/profiles/"; then
  PROFILE_DEST="${NHDW_PROFILE_DIR:-/opt/nhdw-profiles}"
  log "sync country profiles -> $PROFILE_DEST"
  if mkdir -p "$PROFILE_DEST" 2>/dev/null || sudo mkdir -p "$PROFILE_DEST" 2>/dev/null; then
    # --delete so a file removed upstream is removed here too. Plain write
    # first, sudo second: the runner may or may not own the directory, and the
    # script treats sudo as available but not guaranteed.
    # --exclude ACTIVE is required: which profile is active is state, not
    # content, and it is not in the repository. The gateway falls back to
    # <profile dir>/ACTIVE when /opt/nhdw-state/ACTIVE is absent, so a --delete
    # without this exclusion would reset the deployment's country on every
    # profile edit.
    rsync -a --delete --exclude ACTIVE "$REPO/bundle/profiles/" "$PROFILE_DEST/" 2>/dev/null \
      || sudo rsync -a --delete --exclude ACTIVE "$REPO/bundle/profiles/" "$PROFILE_DEST/" \
      || { echo "  FAIL: could not sync profiles to $PROFILE_DEST"; exit 1; }
    echo "  profiles now: $(ls -1 "$PROFILE_DEST" 2>/dev/null | tr '\n' ' ')"
  else
    echo "  FAIL: could not create $PROFILE_DEST"; exit 1
  fi
fi


BUILT=(); RESTART=()
for c in "${COMPONENTS[@]}"; do
  IFS='|' read -r prefix image dockerfile context deploy <<<"$c"
  hit=0
  if [ "$changed_input" = "all" ] || touched "$prefix"; then hit=1; fi
  # the gateway bakes in the validator harness from outside its context, so
  # a change to the canonical source must rebuild it too
  if [ "$image" = "nhdw/gateway:1.0.0" ] && touched "2_pipelines/ml/registry/validators.py"; then hit=1; fi
  if [ "$hit" = 1 ]; then
    # nhdw/gateway bakes in the graduation validator harness (canonical home
    # 2_pipelines/ml/registry/validators.py). COPY cannot reach outside the
    # build context, so stage it (transient, gitignored) as build-images.sh
    # does. Fresh CI checkouts do not carry the staged copy.
    if [ "$image" = "nhdw/gateway:1.0.0" ]; then
      cp "$REPO/2_pipelines/ml/registry/validators.py" \
         "$REPO/6_access-points/api/nhdw-gateway/validators.py"
    fi
    log "build $image  (context $context)"
    if docker build -t "$image" -f "$context/$dockerfile" "$context" >"$RUNTMP"/ci-build-$deploy.log 2>&1; then
      docker save "$image" | sudo k3s ctr images import - >/dev/null 2>&1 || docker save "$image" | k3s ctr images import - >/dev/null 2>&1
      BUILT+=("$image"); RESTART+=("$deploy")
    else
      echo "  FAIL: build failed for $image: see "$RUNTMP"/ci-build-$deploy.log"; tail -20 "$RUNTMP"/ci-build-$deploy.log; exit 1
    fi
  fi
done

# manifest tier: detect undeployed manifest changes (never blind-apply)
# This pipeline does not apply the k8s overlay. The committed
# overlay carries the dev convention and this deployment is re-homed: the
# rendered ingresses say *.127.0.0.1.nip.io against a cluster serving
# *.<public-ip>.nip.io, and the gateway's issuer list renders as
# http://127.0.0.1:8090/realms/... against a live https://keycloak.<host>/...
# Applying it would take every dashboard offline and break JWT validation. The
# host base is supplied by provisioning, not inherited from the repo.
#
# What CI does instead is detect that a manifest change has not reached the
# cluster. A declared workload that does not exist is reported as a hard
# failure, because it means the change did not deploy at all.
if [ "$changed_input" = "all" ] || touched "1_infrastructure/k8s/"; then
  log "check declared workloads against the cluster (manifests are applied by provisioning)"
  SEC_SRC="${NHDW_SECRETS_FILE:-}"
  if [ -f "$SEC_SRC" ]; then
    cp "$SEC_SRC" "$REPO/1_infrastructure/k8s/overlays/dev/secrets.yaml"
    if kustomize build --load-restrictor=LoadRestrictionsNone \
         "$REPO/1_infrastructure/k8s/overlays/dev" > "$RUNTMP"/ci-overlay.yaml 2>/dev/null; then
      MISSING=""
      while read -r kind name; do
        [ -z "$kind" ] && continue
        kubectl -n "$NS" get "$kind" "$name" >/dev/null 2>&1 || MISSING="$MISSING $kind/$name"
      done < <(python3 -c '
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") in ("Deployment", "StatefulSet"):
        print(d["kind"].lower(), d["metadata"]["name"])
' "$RUNTMP"/ci-overlay.yaml)
      if [ -n "$MISSING" ]; then
        echo "  FAIL: declared but not on this cluster:$MISSING"
        echo "    manifests are applied by provisioning (workflow provision.yml /"
        echo "    ansible site.yml), which supplies the host base this overlay does"
        echo "    not carry. Run it, or the change is not deployed."
        exit 1
      fi
      log "  ok: every declared workload exists on the cluster"
    else
      log "  ! overlay render failed: skipping the workload check"
    fi
  else
    log "  ! no secrets.yaml at $SEC_SRC: skipping the workload check"
  fi
fi

# The order below is required: reseed (fills ops_perm) -> OPA policy regen ->
# workload restarts. The gateway's startup self-tests query the live OPA
# (allow/deny matrix and masking reveal sets) and refuse to boot on drift, so
# restarting it before the policy lands would crashloop a gateway that is
# newer than its policy.

RESEED=0
for g in "${RESEED_GLOBS[@]}"; do touched "$g" && RESEED=1; done
if [ "$changed_input" = "all" ] || [ "$RESEED" = 1 ]; then
  log "reseed catalog + app/security (idempotent)"
  # a failed seed must fail the deploy; never swallow the loader's exit code
  if ! bash "$REPO/bundle/scripts/seed-app-security.sh" >"$RUNTMP"/ci-reseed.log 2>&1; then
    echo "  FAIL: catalog/RBAC reseed FAILED"; tail -40 "$RUNTMP"/ci-reseed.log; exit 1
  fi
  tail -5 "$RUNTMP"/ci-reseed.log
fi

# Keycloak users and clients: refresh the seed configmaps from the checkout,
# then re-run the cluster's own job spec, whose env carries this deployment's
# hostBase/DASH_BASE redirect bases. Never apply the base manifest's job here:
# its dev defaults (127.0.0.1.nip.io) would rewrite every client's redirect
# URIs and break login.
if touched "bundle/seed-pack/app/security/users.yaml" \
   || touched "7_security/keycloak/scripts/setup-keycloak.py"; then
  log "reseed keycloak users/clients (host base: ${NHDW_HOSTBASE:-127.0.0.1.nip.io})"
  kubectl -n "$NS" create configmap keycloak-seed-script \
    --from-file="$REPO/7_security/keycloak/scripts/setup-keycloak.py" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
  # the key stays users.yaml (the job mounts that path); the source is the
  # active country's cast, resolved the same way everywhere else.
  USERS_SEED=$(bash "$REPO/bundle/scripts/resolve-users-seed.sh")
  kubectl -n "$NS" create configmap keycloak-users-seed \
    --from-file="users.yaml=$REPO/bundle/seed-pack/app/security/$USERS_SEED" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
  # Render first, delete only once a valid spec is in hand: a render failure
  # after the delete would leave the cluster with no seed Job at all (and the
  # live spec was the only carrier of this deployment's host base).
  RENDER="$REPO/bundle/scripts/render-keycloak-seed-job.py"
  HB="${NHDW_HOSTBASE:-127.0.0.1.nip.io}"
  if kubectl -n "$NS" get job keycloak-seed -o json > /tmp/kc-job-live.json 2>/dev/null; then
    python3 "$RENDER" --hostbase "$HB" --from-live /tmp/kc-job-live.json > /tmp/kc-job.json
  else
    # No live Job (fresh host, or a previous run removed it): render the
    # committed manifest and stamp this deployment's host base on it
    log "  (no live keycloak-seed job: rendering from the base manifest)"
    kustomize build --load-restrictor=LoadRestrictionsNone "$REPO/1_infrastructure/k8s/base" \
      | python3 "$RENDER" --hostbase "$HB" > /tmp/kc-job.json
  fi
  if ! python3 -c "
import json,sys
j=json.load(open('/tmp/kc-job.json'))
env={e['name']: e.get('value') for e in j['spec']['template']['spec']['containers'][0]['env'] if 'value' in e}
assert env.get('PUBLIC_HOST_BASE') == '$HB', f\"host base not stamped: {env.get('PUBLIC_HOST_BASE')}\"
"; then
    echo "  FAIL: rendered keycloak-seed job failed its host-base check; not touching the live job"; exit 1
  fi
  kubectl -n "$NS" delete job keycloak-seed --ignore-not-found --wait=true >/dev/null
  kubectl -n "$NS" apply -f /tmp/kc-job.json >/dev/null
  if ! kubectl -n "$NS" wait --for=condition=complete job/keycloak-seed --timeout=300s >/dev/null 2>&1; then
    echo "  FAIL: keycloak-seed did not complete"; kubectl -n "$NS" logs job/keycloak-seed --tail=20; exit 1
  fi
  kubectl -n "$NS" logs job/keycloak-seed 2>/dev/null | grep -E "users \(|imported" | head -4
fi

# OPA policy: recompile from the (just reseeded) permission store, run opa
# test, publish to the opa-policies ConfigMap and reload, whenever the policy
# source changed.
if [ "$changed_input" = "all" ] || touched "7_security/opa/" || touched "bundle/roles/"; then
  log "regen OPA policy (opa-test gated)"
  if ! bash "$REPO/bundle/scripts/regen-opa-policy.sh" >"$RUNTMP"/ci-opa.log 2>&1; then
    echo "  FAIL: OPA policy regen FAILED (running OPA left untouched)"; tail -25 "$RUNTMP"/ci-opa.log; exit 1
  fi
  grep -E "PASS" "$RUNTMP"/ci-opa.log | tail -4
fi

# restart rebuilt workloads (and the gateway on a reseed, so it reloads the
# catalog/RBAC caches) now that the policy matches the code
if [ "$RESEED" = 1 ] || [ "$changed_input" = "all" ]; then
  case " ${RESTART[*]} " in
    *" nhdw-gateway "*) : ;;                       # already being restarted
    *) RESTART+=("nhdw-gateway") ;;
  esac
fi
for d in "${RESTART[@]}"; do
  log "rollout restart deploy/$d"
  kubectl -n "$NS" rollout restart "deploy/$d" >/dev/null 2>&1 || true
done
for d in "${RESTART[@]}"; do
  # a workload that never becomes ready (e.g. the gateway failing fast on
  # policy drift) must fail the deploy
  if ! kubectl -n "$NS" rollout status "deploy/$d" --timeout=180s 2>&1 | tail -1; then
    echo "  FAIL: deploy/$d did not become ready"
    kubectl -n "$NS" logs "deploy/$d" --tail=20 2>/dev/null | sed 's/^/    /'
    exit 1
  fi
done

# data tier (images + job chain; DDL and vocabulary applied above)

# 3. Pipeline images and the dbt job chain. Map: <path-prefix>|<image>|<job>|<stage#>
#    Stage order mirrors seed-realistic's PIPELINE; a change at stage N reruns
#    N..marts (downstream models read upstream schemas; marts always last so the
#    report views the vault run drops are restored).
PIPELINE=(
  "2_pipelines/staging|nhdw/staging-dbt:1.0.0|dbt-staging|1"
  "2_pipelines/vault/bridge|nhdw/vault-bridge:1.0.0|bridge-sync|2"
  "2_pipelines/vault|nhdw/vault-dbt:1.0.0|dbt-vault|3"
  "2_pipelines/marts|nhdw/marts-dbt:1.0.0|dbt-marts|4"
)
first_stage=99
PIPE_BUILT=()
for p in "${PIPELINE[@]}"; do
  IFS='|' read -r prefix image job stage <<<"$p"
  hit=0
  if [ "$changed_input" = "all" ] || touched "$prefix"; then hit=1; fi
  # bridge is a subpath of vault: a pure-bridge change must not also count as vault
  if [ "$hit" = 1 ] && [ "$changed_input" != "all" ] && [ "$prefix" = "2_pipelines/vault" ] \
     && ! echo "$CHANGED" | grep -E '^2_pipelines/vault' | grep -vq '^2_pipelines/vault/bridge'; then hit=0; fi
  if [ "$hit" = 1 ]; then
    log "build $image"
    if docker build -t "$image" -f "$REPO/$prefix/Dockerfile" "$REPO/$prefix" >"$RUNTMP"/ci-build-$job.log 2>&1; then
      docker save "$image" | sudo k3s ctr images import - >/dev/null 2>&1 \
        || docker save "$image" | k3s ctr images import - >/dev/null 2>&1
      PIPE_BUILT+=("$image")
      [ "$stage" -lt "$first_stage" ] && first_stage=$stage
    else
      echo "  FAIL: build failed for $image: see "$RUNTMP"/ci-build-$job.log"; tail -20 "$RUNTMP"/ci-build-$job.log; exit 1
    fi
  fi
done
# vocabulary or vault-side DDL changes re-derive the vault even with no image rebuilt
{ touched "bundle/seeds/ref-vocab/" || [ ${#DDL_VAULT[@]} -gt 0 ]; } && [ "$first_stage" -gt 3 ] && first_stage=3

run_job(){ # $1=job name  $2=timeout
  kustomize build --load-restrictor=LoadRestrictionsNone "$REPO/1_infrastructure/k8s/jobs" | \
    python3 -c "
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get('kind')=='Job' and d['metadata']['name']=='$1':
        print(yaml.safe_dump(d)); break
" > "$RUNTMP"/ci-job-$1.yaml
  [ -s "$RUNTMP"/ci-job-$1.yaml ] || { echo "  FAIL: job $1 not found in the jobs kustomization"; exit 1; }
  log "rerun job $1"
  kubectl -n "$NS" delete job "$1" --ignore-not-found >/dev/null 2>&1
  kubectl -n "$NS" apply -f "$RUNTMP"/ci-job-$1.yaml >/dev/null
  # Wait for either terminal condition. `kubectl wait --for=condition=complete`
  # ignores a Failed job and burns the whole timeout. Poll both, and report
  # the failure as soon as it happens.
  local waited=0 state=""
  while [ "$waited" -lt "$2" ]; do
    if ! kubectl -n "$NS" get "job/$1" >/dev/null 2>&1; then state="gone"; break; fi
    if [ "$(kubectl -n "$NS" get "job/$1" -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null)" = "True" ]; then
      state="complete"; break
    fi
    if [ "$(kubectl -n "$NS" get "job/$1" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null)" = "True" ]; then
      state="failed"; break
    fi
    sleep 10; waited=$((waited + 10))
  done
  if [ "$state" != "complete" ]; then
    case "$state" in
      failed) echo "  FAIL: job $1 FAILED after ${waited}s" ;;
      gone)   echo "  FAIL: job $1 no longer EXISTS: deleted outside this deploy while we waited" ;;
      *)      echo "  FAIL: job $1 timed out after ${2}s (still running)" ;;
    esac
    # a job that still exists can still be asked for its logs (one deleted
    # mid-wait from outside this deploy is reported above)
    [ "$state" != "gone" ] && kubectl -n "$NS" logs "job/$1" --tail=25
    exit 1
  fi
}

if [ "$first_stage" -le 4 ]; then
  [ "$first_stage" -le 1 ] && run_job dbt-staging 1200
  [ "$first_stage" -le 2 ] && run_job bridge-sync 1800
  [ "$first_stage" -le 3 ] && run_job dbt-vault 2400
  run_job dbt-marts 2400
fi

# 4. MDM side-lane. Rebuild on its own change; rerun the candidate generation
#    whenever its code changed OR the vault content moved (candidates derive
#    from the resolution view). Idempotent: decided candidates are never touched.
MDM_RUN=0
if [ "$changed_input" = "all" ] || touched "2_pipelines/mdm/"; then
  log "build nhdw/mdm-matcher:1.0.0"
  if docker build -t nhdw/mdm-matcher:1.0.0 -f "$REPO/2_pipelines/mdm/Dockerfile" "$REPO/2_pipelines/mdm" >"$RUNTMP"/ci-build-mdm-matcher.log 2>&1; then
    docker save nhdw/mdm-matcher:1.0.0 | sudo k3s ctr images import - >/dev/null 2>&1 \
      || docker save nhdw/mdm-matcher:1.0.0 | k3s ctr images import - >/dev/null 2>&1
    PIPE_BUILT+=("nhdw/mdm-matcher:1.0.0"); MDM_RUN=1
  else
    echo "  FAIL: build failed for nhdw/mdm-matcher:1.0.0"; tail -20 "$RUNTMP"/ci-build-mdm-matcher.log; exit 1
  fi
fi
[ "$first_stage" -le 3 ] && MDM_RUN=1
[ "$MDM_RUN" = 1 ] && run_job mdm-matcher 420

log "verify"
if [ -x "$REPO/bundle/scripts/verify-system.sh" ]; then
  bash "$REPO/bundle/scripts/verify-system.sh" 2>&1 | tail -20 || echo "  (verify reported issues: review above)"
fi
log "done. app built: ${BUILT[*]:-none}  pipeline built: ${PIPE_BUILT[*]:-none}  ddl: ${DDL_APPLIED[*]:-none}  reseed: $([ "$RESEED" = 1 ] || [ "$changed_input" = all ] && echo yes || echo no)"
