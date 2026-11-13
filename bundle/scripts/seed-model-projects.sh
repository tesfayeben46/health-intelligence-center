#!/usr/bin/env bash
# seed-model-projects.sh: bootstrap step 7.58. Makes each platform ML model an
# approved workshop project through the gateway API, never by INSERTing
# ops_workspace.project, ops_artifact.artifact or project_review rows.
#
# The platform models (listed in MODELS below) are already trained and
# integrated by seed-ml.sh (MLflow @champion, ops_ml.model_deployment, first
# predictions). Their seed packages live under bundle/seed-pack/app/ml/<model>/
# (train.py, manifest.yaml, feature_spec.yaml, model_card.md, params.yaml,
# requirements.txt, tests/). This seed gives each one a project:
#   • a project row (created via POST /api/projects as the model owner),
#   • its package code in the project's object-store files (projects/<id>/files/model/),
#   • a kind='model' artifact bound to the existing MLflow model, and
#   • an approved review record, with the project published (public).
# POST /api/graduation/project/<id>/adopt-model performs the artifact and review
# writes server-side; it does not retrain, fork or start a runner.
#
# Convergent, like seed-graduation.sh: each project keys off its current
# ops_workspace.project.review_status and its object-store file listing, so a
# partly done or complete boot resumes the missing step and re-running creates
# no duplicates. adopt-model is idempotent (reuses the existing artifact and
# verdict row and re-asserts approved and public).
#
# Reaches the gateway as seed-graduation.sh does: a host-side
# `kubectl port-forward svc/nhdw-gateway`, waited on by polling /health. Auth is
# the demo impersonation channel (GATEWAY_DEMO_IMPERSONATION=true): X-Mock-User
# is a seeded ops_identity.user.kc_sub (owner and reviewer columns reference it),
# plus X-Mock-Role and the shared X-Mock-Secret. Accumulates failures and exits
# non-zero so bootstrap gates on it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
ML_SEEDS="$REPO/bundle/seed-pack/app/ml"
GW_LOCAL_PORT="${MODEL_PROJECTS_GW_PORT:-18083}"
export PATH="$HOME/.local/bin:$PATH"

# Seeded principals (ops_identity.user.kc_sub). Owner and reviewer columns have
# FKs to ops_identity.user, so the impersonation header must carry a seeded sub;
# an unknown UUID fails the FK with a 500. Same values as seed-graduation.sh.
SCI_SUB=44444444-aaaa-bbbb-cccc-dddddddddddd;  SCI_ROLE=R-CONS-SCI   # sci.demo (scientist), owner
STEW_SUB=bbbbbbbb-cccc-dddd-eeee-ffffffffffff; STEW_ROLE=R-STEW      # demo.steward (reviewer)

step() { echo; echo "▸ $*"; }
ok()   { echo "  ok: $*"; }
warn() { echo "  ! $*" >&2; }
FAILED=0

echo "== model-projects seed (5 platform models -> approved public projects, real adopt-model API) =="

# gateway reachability: port-forward like seed-graduation.sh
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

# helpers (duplicated from seed-graduation.sh rather than shared)
json_get() { # <key> ; reads a JSON object on stdin, prints obj[key] ('' if absent)
  python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get(sys.argv[1],'') if isinstance(d,dict) else '')" "$1" 2>/dev/null
}

psql_one() { # <sql>: first column of the first row ('' if none)
  kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
    psql -U vault_admin -d nhdw_ops -At -c "$1" 2>/dev/null | head -1
}

project_id_by_name() { # <name>: project uuid ('' if absent)
  psql_one "SELECT id FROM ops_workspace.project WHERE name = \$\$$1\$\$ ORDER BY created_at LIMIT 1"
}
review_status_of() { # <project_id>: review_status ('' if absent)
  psql_one "SELECT review_status FROM ops_workspace.project WHERE id = '$1'::uuid"
}

gw() { # <method> <path> <sub> <role> [json-body]: response body on stdout
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
ensure_project() { # <name> <desc> <visibility> <sub> <role>: echoes project_id
  local name="$1" desc="$2" vis="$3" sub="$4" role="$5" pid resp
  pid=$(project_id_by_name "$name")
  if [ -n "$pid" ]; then echo "$pid"; return 0; fi
  resp=$(gw POST /api/projects "$sub" "$role" \
    "{\"name\":\"$name\",\"description\":\"$desc\",\"visibility\":\"$vis\"}")
  echo "$resp" | json_get id
}

# ensure the model's package files are uploaded (idempotent)
# Uploads every file under <src_dir>/ into projects/<id>/files/model/, preserving
# the relative folder (so tests/ stays nested and manifest.yaml lands at
# model/manifest.yaml). Skips when the manifest is already present (a prior boot
# uploaded it). Uses the upload API.
upload_files_from() { # <src_dir> <project_id> <sub> <role>
  local dir="$1" pid="$2" sub="$3" role="$4" f rel sub_rel folder fname resp
  # convergence: manifest already in the listing means the files are uploaded
  if gw GET "/api/projects/$pid/files" "$sub" "$role" | grep -q '"model/manifest.yaml"'; then
    ok "model files already in projects/$pid/files/model/ (convergent)"; return 0
  fi
  while IFS= read -r f; do
    rel="${f#"$dir"/}"                          # e.g. train.py or tests/test_x.py
    sub_rel="$(dirname "$rel")"
    if [ "$sub_rel" = "." ]; then folder="model"; else folder="model/$sub_rel"; fi
    fname="$(basename "$rel")"
    resp=$(curl -s --max-time 60 -X POST "$GW/api/projects/$pid/files/upload" \
      -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET" \
      -F "path=$folder" -F "file=@$f;filename=$fname")
    [ -n "$(echo "$resp" | json_get name)" ] || { warn "upload FAILED ($rel): $resp"; return 1; }
  done < <(find "$dir" -type f | sort)
  ok "uploaded model files -> projects/$pid/files/model/"
}

# platform models: model_key (folder under bundle/seed-pack/app/ml) | project
# name | one-line description
MODELS=$(cat <<'EOF'
ewars_lite|EWARS-lite Malaria Early-Warning|Climate-driven early-warning for malaria over the weekly IDSR mart.
ears|EARS Aberration Detector|CDC EARS C1/C2/C3 aberration detection over weekly notifiable-disease counts.
seasonal|Seasonal Disease Forecaster|Seasonal-baseline forecaster for notifiable diseases.
outbreak|Outbreak Risk Detector|Farrington outbreak-risk detector over weekly cases + climate.
stockout_forecast|Commodity Stockout Forecast|Days-to-stockout forecaster over the monthly supply mart.
elmis_consumption_forecast|PCT Consumption Forecast|One-month-ahead demand forecast per facility and tracer product over the eLMIS supply mart (P10/P50/P90).
EOF
)

# A here-string while-loop (not a pipe), so updates to FAILED persist outside the loop.
while IFS='|' read -r key name desc; do
  [ -n "$key" ] || continue
  step "model project: $name  [adopt platform model '$key' -> approved/public]"
  src="$ML_SEEDS/$key"
  if [ ! -d "$src" ]; then warn "seed package missing: $src"; FAILED=1; continue; fi

  pid=$(ensure_project "$name" "$desc" "private" "$SCI_SUB" "$SCI_ROLE")
  if [ -z "$pid" ]; then warn "could not create/find '$name'"; FAILED=1; continue; fi
  ok "project '$name' -> $pid"

  upload_files_from "$src" "$pid" "$SCI_SUB" "$SCI_ROLE" || FAILED=1

  cur=$(review_status_of "$pid")
  if [ "$cur" = "approved" ]; then
    ok "already approved (convergent): leaving as is"
    continue
  fi

  resp=$(gw POST "/api/graduation/project/$pid/adopt-model" "$STEW_SUB" "$STEW_ROLE" \
    "{\"model_name\":\"$key\",\"title\":\"$name\",\"note\":\"Platform model adopted onto its project.\"}")
  if [ "$(echo "$resp" | json_get review_status)" = "approved" ] \
     && [ "$(echo "$resp" | json_get adopted)" = "True" ]; then
    ok "adopted -> approved/public (artifact $(echo "$resp" | json_get artifact_id))"
  else
    warn "adopt-model FAILED for '$name': $resp"; FAILED=1
  fi
done <<< "$MODELS"

if [ "$FAILED" -ne 0 ]; then echo; echo "== model-projects seed FAILED (see ! above) =="; exit 1; fi
echo; echo "== model-projects seed complete (6 platform models -> approved public projects) =="
