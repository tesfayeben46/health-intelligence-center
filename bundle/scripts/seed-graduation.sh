#!/usr/bin/env bash
# seed-graduation.sh: boot step 7.6. Populates the project-centric graduation
# (public-review) flow through the gateway API a contributor and reviewer use,
# never by inserting ops_workspace.project / project_review rows directly.
#
# The unit of review is the project. This seed leaves three example community
# projects, each owned by a seeded contributor and carrying a complete runner
# package under projects/<id>/files/model/, spanning the review lifecycle:
#
#   1) Community Malaria Outbreak-Risk: submit -> approve -> approved + public
#        └ its model (community_malaria_risk) is integrated by the graduation
#          hook the approve endpoint fires: MLflow @champion, a model_deployment
#          row and first predictions, so it shows in the shared model catalogue
#          (GET /api/ml).
#   2) Community EWARS Malaria E-W: submit only -> in_review
#        └ feeds the reviewer queue (GET /api/graduation/project-queue).
#   3) Community Stockout Forecast: submit -> reject (changes) -> changes_requested
#
# Convergent (like seed-ml.sh "skip if @champion" and seed-projects.sh "skip if
# name present"): each project's target is keyed off its current
# ops_workspace.project.review_status, so a half-done or complete boot resumes
# the missing step and a re-run creates no duplicates. The approved model is
# (re)materialised idempotently, so it is a catalogue-visible model even
# offline at cold boot without anyone clicking approve.
#
# Reaches the gateway as seed-projects.sh does: a host-side
# `kubectl port-forward svc/nhdw-gateway`, waited on by polling /health. Auth is
# the impersonation channel (GATEWAY_DEMO_IMPERSONATION=true): X-Mock-User is a
# seeded ops_identity.user.kc_sub (owner columns and reviewer reference it),
# plus X-Mock-Role and the shared X-Mock-Secret. Failures accumulate and the
# script exits non-zero, so bootstrap gates on it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
SEEDS="$REPO/bundle/seed-pack/app/project-review"
GW_LOCAL_PORT="${GRADUATION_GW_PORT:-18082}"
RUNNER_IMAGE="${ML_RUNNER_IMAGE:-nhdw/ml-runner:1.0.0}"
export PATH="$HOME/.local/bin:$PATH"

# Seeded principals (ops_identity.user.kc_sub). Owner columns and the reviewer
# reference ops_identity.user, so the impersonation header must carry a seeded
# sub; any other UUID fails the FK with a 500.
RES_SUB=88888888-9999-aaaa-bbbb-cccccccccccc;  RES_ROLE=R-CONS-RES   # genet.e (researcher)
SCI_SUB=44444444-aaaa-bbbb-cccc-dddddddddddd;  SCI_ROLE=R-CONS-SCI   # sci.demo (scientist)
PRT_SUB=99999999-aaaa-bbbb-cccc-dddddddddddd;  PRT_ROLE=R-CONS-PRT   # shewaye.a (partner)
STEW_SUB=bbbbbbbb-cccc-dddd-eeee-ffffffffffff; STEW_ROLE=R-STEW      # demo.steward (reviewer)

step() { echo; echo "▸ $*"; }
ok()   { echo "  ok: $*"; }
warn() { echo "  ! $*" >&2; }
FAILED=0

echo "== graduation seed (PROJECT-centric: submit -> approve/reject via the real gateway API) =="

# gateway reachability: port-forward like seed-projects.sh
kubectl -n "$NS" port-forward svc/nhdw-gateway "${GW_LOCAL_PORT}:8000" >/dev/null 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null' EXIT
GW="http://127.0.0.1:${GW_LOCAL_PORT}"
up=0
for _ in $(seq 1 24); do
  if curl -s -o /dev/null --max-time 2 "$GW/health"; then up=1; break; fi
  sleep 2
done
[ "$up" = 1 ] || { warn "gateway not reachable on $GW/health after ~48s"; exit 1; }
ok "gateway reachable ($GW)"

PGPW=$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
# Shared secret gating the X-Mock-* impersonation channel (base/nhdw-gateway.yaml).
IMP_SECRET=$(kubectl -n "$NS" get secret nhdw-gateway-secrets -o jsonpath='{.data.IMPERSONATION_SECRET}' | base64 -d 2>/dev/null)

# helpers
json_get() { # <key> ; reads a JSON object on stdin, prints obj[key] ('' if absent)
  python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get(sys.argv[1],'') if isinstance(d,dict) else '')" "$1" 2>/dev/null
}

json_path() { # <dotted.path> ; reads JSON on stdin, prints nested value ('' if absent)
  python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: d={}
for k in sys.argv[1].split('.'):
    d = d.get(k, {}) if isinstance(d, dict) else {}
print('' if d in ({}, None) else d)" "$1" 2>/dev/null
}

psql_one() { # <sql> -> first column of the first row ('' if none)
  kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
    psql -U vault_admin -d nhdw_ops -At -c "$1" 2>/dev/null | head -1
}

project_id_by_name() { # <name> -> project uuid ('' if absent)
  psql_one "SELECT id FROM ops_workspace.project WHERE name = \$\$$1\$\$ ORDER BY created_at LIMIT 1"
}
review_status_of() { # <project_id> -> review_status ('' if absent)
  psql_one "SELECT review_status FROM ops_workspace.project WHERE id = '$1'::uuid"
}
pred_count() { # <model_name> -> count of successful-run predictions (0 if none)
  local n; n=$(psql_one "SELECT count(*) FROM ops_ml.ml_predictions p JOIN ops_ml.ml_run r ON r.id=p.run_id WHERE r.model_name='$1' AND r.status='success'")
  echo "${n:-0}"
}
has_champion() { # <model_name> -> 0 iff an @champion alias is registered in MLflow
  kubectl -n "$NS" exec deploy/nhdw-gateway -- python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://mlflow.nhdw.svc.cluster.local:5000/api/2.0/mlflow/registered-models/alias?name=$1&alias=champion')
    print('yes')
except Exception:
    print('no')" 2>/dev/null | grep -q yes
}

gw() { # <method> <path> <sub> <role> [json-body] -> response body on stdout
  local method="$1" path="$2" sub="$3" role="$4" body="${5:-}"
  if [ -n "$body" ]; then
    curl -s --max-time 120 -X "$method" "$GW$path" \
      -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET" \
      -H "Content-Type: application/json" -d "$body"
  else
    curl -s --max-time 120 -X "$method" "$GW$path" \
      -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET"
  fi
}

# ensure a project exists (create as its owner if absent)
ensure_project() { # <name> <desc> <visibility> <sub> <role> -> echoes project_id
  local name="$1" desc="$2" vis="$3" sub="$4" role="$5" pid resp
  pid=$(project_id_by_name "$name")
  if [ -n "$pid" ]; then echo "$pid"; return 0; fi
  resp=$(gw POST /api/projects "$sub" "$role" \
    "{\"name\":\"$name\",\"description\":\"$desc\",\"visibility\":\"$vis\"}")
  echo "$resp" | json_get id
}

# ensure the project's package files are uploaded (idempotent)
# Uploads every file under $SEEDS/<slug>/ into projects/<id>/files/, preserving
# the relative folder (model/ stays nested). Skips when the manifest is already
# present (a prior boot uploaded it). Uses the gateway upload API.
upload_files() { # <slug> <project_id> <sub> <role>
  local slug="$1" pid="$2" sub="$3" role="$4" f rel folder fname resp
  local dir="$SEEDS/$slug"
  # convergence: manifest already in the listing -> files already uploaded
  if gw GET "/api/projects/$pid/files" "$sub" "$role" | grep -q '"model/manifest.yaml"'; then
    ok "package files already in projects/$pid/files/ (convergent)"; return 0
  fi
  while IFS= read -r f; do
    rel="${f#"$dir"/}"                         # e.g. model/manifest.yaml or README.md
    folder="$(dirname "$rel")"; [ "$folder" = "." ] && folder=""
    fname="$(basename "$rel")"
    resp=$(curl -s --max-time 60 -X POST "$GW/api/projects/$pid/files/upload" \
      -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET" \
      -F "path=$folder" -F "file=@$f;filename=$fname")
    [ -n "$(echo "$resp" | json_get name)" ] || { warn "upload FAILED ($rel): $resp"; return 1; }
  done < <(find "$dir" -type f | sort)
  ok "uploaded package files -> projects/$pid/files/"
}

# submit a project for review (owner)
submit_project() { # <project_id> <sub> <role> -> 0 iff review_status reached in_review
  local pid="$1" sub="$2" role="$3" resp
  resp=$(gw POST "/api/graduation/project/$pid/submit" "$sub" "$role")
  [ "$(echo "$resp" | json_get review_status)" = "in_review" ] \
    || { warn "submit FAILED: $resp"; return 1; }
}

# reject / request-changes (reviewer)
request_changes() { # <project_id> <note> -> 0 iff review_status reached changes_requested
  local pid="$1" note="$2" resp
  resp=$(gw POST "/api/graduation/project/$pid/reject" "$STEW_SUB" "$STEW_ROLE" \
    "{\"note\":\"$note\",\"changes_requested\":true}")
  [ "$(echo "$resp" | json_get review_status)" = "changes_requested" ] \
    || { warn "request-changes FAILED: $resp"; return 1; }
}

# self-contained ml-runner Job builder (mirrors seed-ml.sh): the fallback
# that makes the approved community model materialise even if the approve
# hook's async Job was lost (offline cold boot).
run_runner_job() { # <job-name> <verb> <args...>
  local job="$1"; shift
  kubectl -n "$NS" delete job "$job" --ignore-not-found --wait=true >/dev/null 2>&1
  kubectl -n "$NS" apply -f - >/dev/null << EOF
apiVersion: batch/v1
kind: Job
metadata: { name: $job, namespace: $NS, labels: { app: ml-runner, tier: ml } }
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 3600
  template:
    metadata: { labels: { app: ml-runner, tier: ml } }
    spec:
      restartPolicy: Never
      containers:
        - name: runner
          image: $RUNNER_IMAGE
          imagePullPolicy: IfNotPresent
          args: [$(printf '"%s",' "$@" | sed 's/,$//')]
          env:
            - { name: MLFLOW_TRACKING_URI, value: "http://mlflow.nhdw.svc.cluster.local:5000" }
            - { name: MLFLOW_S3_ENDPOINT_URL, value: "http://seaweedfs-s3.nhdw.svc.cluster.local:8333" }   # SeaweedFS S3 API
            - { name: MINIO_ENDPOINT,        value: "http://seaweedfs-s3.nhdw.svc.cluster.local:8333" }
            - { name: BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE, value: "true" }
            - { name: AWS_DEFAULT_REGION, value: "et-addis-1" }
            - { name: AWS_ACCESS_KEY_ID,     valueFrom: { secretKeyRef: { name: mlflow-secrets, key: S3_ACCESS_KEY } } }
            - { name: AWS_SECRET_ACCESS_KEY, valueFrom: { secretKeyRef: { name: mlflow-secrets, key: S3_SECRET_KEY } } }
            - { name: MINIO_ACCESS_KEY,      valueFrom: { secretKeyRef: { name: minio-secrets, key: ROOT_USER } } }
            - { name: MINIO_SECRET_KEY,      valueFrom: { secretKeyRef: { name: minio-secrets, key: ROOT_PASSWORD } } }
            - { name: OPS_PG_HOST, value: "postgres-vault.nhdw.svc.cluster.local" }
            - { name: VAULT_ADMIN_PASSWORD,  valueFrom: { secretKeyRef: { name: postgres-vault-secrets, key: POSTGRES_PASSWORD } } }
EOF
  kubectl -n "$NS" wait --for=condition=complete "job/$job" --timeout=600s >/dev/null 2>&1
}

# Materialise the community package (the manifest is already community-named,
# so no rewrite is needed) into ml-packages/<model>/current/, upsert its
# model_deployment row, then train and predict (the seed-ml.sh sequence). Used
# only as the fallback when the approve hook's async Job did not materialise.
backstop_integrate() { # <slug> <model_name>
  local slug="$1" model="$2" k="${2//_/-}"
  step "backstop integration for $model (hook path did not materialize it)"
  . "$REPO/bundle/scripts/lib/objstore.sh"
  objstore_mc    || { warn "no mc client: cannot run backstop"; return 1; }
  objstore_creds || { warn "object-store credentials not readable"; return 1; }
  objstore_pf 19010
  objstore_alias
  "$MC" --config-dir "$OS_MC_CFG" mirror --quiet --overwrite "$SEEDS/$slug/model" \
        "L/nhdw-artifacts/ml-packages/$model/current" >/dev/null \
    && ok "package -> ml-packages/$model/current/" || warn "package mirror FAILED"
  objstore_pf_stop
  # upsert the deployment row (schedule mirrors the seeded models)
  kubectl -n "$NS" exec -i postgres-vault-0 -- env PGPASSWORD="$PGPW" psql -U vault_admin -d nhdw_ops -v ON_ERROR_STOP=1 -q >/dev/null 2>&1 << SQL || warn "deployment upsert FAILED"
INSERT INTO ops_ml.model_deployment (model_name, model_alias, deployment_kind, schedule, enabled, params)
VALUES ('$model','champion','batch','0 4 * * 1', true,
        '{"package_ref":"minio://nhdw-artifacts/ml-packages/$model/current/","source":"project-review-seed"}'::jsonb)
ON CONFLICT (model_name) DO UPDATE SET model_alias='champion', deployment_kind='batch',
  schedule='0 4 * * 1', enabled=true, updated_at=now();
SQL
  if has_champion "$model"; then ok "@champion already registered: skipping train"
  else run_runner_job "ml-train-$k" train "minio://nhdw-artifacts/ml-packages/$model/current/" \
         && ok "trained + registered @champion" || { warn "TRAIN FAILED for $model"; return 1; }
  fi
  run_runner_job "ml-predict-$k" predict-batch "$model" \
    && ok "predict-batch complete ($(pred_count "$model") rows)" \
    || { warn "PREDICT FAILED for $model"; return 1; }
}

# Ensure the approved project's model is fully integrated (registry @champion +
# predictions). Prefer the hook's async Job (the runtime path); wait for it, and
# fall back to backstop_integrate only if it did not materialise.
ensure_integrated() { # <slug> <model_name> <hook_job_name>
  local slug="$1" model="$2" job="$3" i
  if [ -n "$job" ]; then
    step "waiting for the approve hook's integration Job ($job)"
    kubectl -n "$NS" wait --for=condition=complete "job/$job" --timeout=600s >/dev/null 2>&1 \
      && ok "hook integration Job complete" || warn "hook Job not complete in 600s, will verify/back-stop"
  fi
  for i in $(seq 1 6); do
    if has_champion "$model" && [ "$(pred_count "$model")" -gt 0 ]; then
      ok "$model is catalogue-ready (@champion + $(pred_count "$model") predictions)"; return 0
    fi
    sleep 5
  done
  backstop_integrate "$slug" "$model" || return 1
  if has_champion "$model" && [ "$(pred_count "$model")" -gt 0 ]; then
    ok "$model integrated via backstop (@champion + $(pred_count "$model") predictions)"; return 0
  fi
  warn "$model still not catalogue-ready after backstop"; return 1
}

# project 1/3: Community Malaria Outbreak-Risk -> approved + public + integrated
P1_NAME="Community Malaria Outbreak-Risk"
P1_SLUG=community-malaria-risk
P1_MODEL=community_malaria_risk
step "project 1/3: $P1_NAME  [submit -> approve -> approved/public + integrated model]"
pid=$(ensure_project "$P1_NAME" \
      "Community-contributed Farrington outbreak-risk detector over weekly malaria cases + climate (bi_idsr mart)." \
      "private" "$RES_SUB" "$RES_ROLE")
if [ -z "$pid" ]; then warn "could not create/find '$P1_NAME'"; FAILED=1
else
  upload_files "$P1_SLUG" "$pid" "$RES_SUB" "$RES_ROLE" || FAILED=1
  cur=$(review_status_of "$pid")
  case "$cur" in
    draft|"") submit_project "$pid" "$RES_SUB" "$RES_ROLE" && cur=in_review || FAILED=1 ;;
  esac
  case "$cur" in
    in_review|changes_requested)
      resp=$(gw POST "/api/graduation/project/$pid/approve" "$STEW_SUB" "$STEW_ROLE" \
             "{\"comment\":\"Reproduced the notebook and validated the mart query, graduating.\"}")
      if [ "$(echo "$resp" | json_get review_status)" = "approved" ]; then
        ok "approved -> public (integration.integrated=$(echo "$resp" | json_path integration.integrated))"
        job=$(echo "$resp" | json_path integration.steps.train_job)
        ensure_integrated "$P1_SLUG" "$P1_MODEL" "$job" || FAILED=1
      else warn "approve FAILED: $resp"; FAILED=1; fi ;;
    approved)
      ok "already approved (convergent): verifying the model is still catalogue-ready"
      ensure_integrated "$P1_SLUG" "$P1_MODEL" "" || FAILED=1 ;;
    *) warn "unexpected review_status='$cur' for '$P1_NAME'; not overriding" ;;
  esac
fi

# project 2/3: Community EWARS Malaria Early-Warning -> in_review (queue item)
P2_NAME="Community EWARS Malaria Early-Warning"
P2_SLUG=community-ewars-malaria
step "project 2/3: $P2_NAME  [submit only -> in_review (feeds the reviewer queue)]"
pid=$(ensure_project "$P2_NAME" \
      "Community-contributed climate-driven NB2 GLM early-warning for malaria (bi_idsr mart). Awaiting review." \
      "private" "$SCI_SUB" "$SCI_ROLE")
if [ -z "$pid" ]; then warn "could not create/find '$P2_NAME'"; FAILED=1
else
  upload_files "$P2_SLUG" "$pid" "$SCI_SUB" "$SCI_ROLE" || FAILED=1
  cur=$(review_status_of "$pid")
  case "$cur" in
    draft|"") submit_project "$pid" "$SCI_SUB" "$SCI_ROLE" && ok "submitted -> in_review" || FAILED=1 ;;
    *)        ok "already at review_status=$cur (convergent): leaving in the queue lifecycle" ;;
  esac
fi

# project 3/3: Community Stockout Forecast -> changes_requested
P3_NAME="Community Stockout Forecast"
P3_SLUG=community-stockout
step "project 3/3: $P3_NAME  [submit -> request-changes -> changes_requested]"
pid=$(ensure_project "$P3_NAME" \
      "Community-contributed Holt-Winters days-to-stockout forecast over the monthly supply mart. Reviewer feedback pending." \
      "private" "$PRT_SUB" "$PRT_ROLE")
if [ -z "$pid" ]; then warn "could not create/find '$P3_NAME'"; FAILED=1
else
  upload_files "$P3_SLUG" "$pid" "$PRT_SUB" "$PRT_ROLE" || FAILED=1
  cur=$(review_status_of "$pid")
  case "$cur" in
    draft|"") if submit_project "$pid" "$PRT_SUB" "$PRT_ROLE"; then cur=in_review; else FAILED=1; fi ;;
  esac
  case "$cur" in
    in_review)         request_changes "$pid" \
                         "Please add a held-out backtest of the days-to-stockout projection and a units note in the model card before graduation." \
                         && ok "review requested changes -> changes_requested" || FAILED=1 ;;
    changes_requested) ok "already changes_requested (convergent)" ;;
    approved|rejected) ok "already at review_status=$cur: not overriding the reviewer decision" ;;
  esac
fi

if [ "$FAILED" -ne 0 ]; then echo; echo "== graduation seed FAILED (see ! above) =="; exit 1; fi
echo; echo "== graduation seed complete (projects: 1 approved+integrated · 1 in_review · 1 changes_requested) =="
