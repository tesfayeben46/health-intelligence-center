#!/usr/bin/env bash
# seed-projects.sh: bootstrap step 7.55. Creates the demo projects and generated
# datasets through the gateway APIs, never by INSERTing rows. No dataset or
# model exists without a project, so this runs before seed-graduation.sh, whose
# submissions bind to these projects by name.
#
# Leaves three projects covering the visibility model and three generated
# datasets covering the tier model:
#   P1 EWARS Disease Forecasting   (scientist, group  = EWARS Working Group)
#      └ D1 disease-weekly extract     (tier shared with that group)
#   P2 Supply Chain Analytics      (scientist, public)
#      └ D2 regional stock snapshot    (tier public)
#   P3 Clinical Screening & Triage (analyst,   private)
#      └ D3 programme-cascade baseline (tier personal)
#
# Convergent: a project whose name (or a dataset whose title) already exists is
# skipped or resumed, so re-running on every boot creates no duplicates.
# Reaches the gateway as seed-graduation.sh does: a host-side port-forward and
# the X-Mock impersonation channel with seeded ops_identity subs. Accumulates
# failures and exits non-zero so bootstrap gates on it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
GW_LOCAL_PORT="${PROJECTS_GW_PORT:-18081}"
export PATH="$HOME/.local/bin:$PATH"

# Seeded principals (ops_identity.user.kc_sub, referenced by the owner-column FKs).
SCI_SUB=44444444-aaaa-bbbb-cccc-dddddddddddd;  SCI_ROLE=R-CONS-SCI
ANA_SUB=33333333-aaaa-bbbb-cccc-dddddddddddd;  ANA_ROLE=R-CONS-ANA

step() { echo; echo "▸ $*"; }
ok()   { echo "  ok: $*"; }
warn() { echo "  ! $*" >&2; }
FAILED=0

echo "== projects + datasets seed (real /api/projects + /api/datasets APIs) =="

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
IMP_SECRET=$(kubectl -n "$NS" get secret nhdw-gateway-secrets -o jsonpath='{.data.IMPERSONATION_SECRET}' | base64 -d 2>/dev/null)

json_get() { # <key> ; reads a JSON object on stdin, prints obj[key] ('' if absent)
  python3 -c "import sys,json
key=sys.argv[1]
try:
    d=json.load(sys.stdin)
except Exception:
    d={}
print(d.get(key,'') if isinstance(d,dict) else '')" "$1" 2>/dev/null
}

psql_one() { # <sql>: first column of the first row ('' if none)
  kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
    psql -U vault_admin -d nhdw_ops -At -c "$1" 2>/dev/null | head -1
}

# projects
PRJ_ID=""   # ensure_project's out-param (logs go to fd1, so no $() capture)
ensure_project() { # <name> <desc> <visibility private|public> <sub> <role>
  local name="$1" desc="$2" vis="$3" sub="$4" role="$5" resp
  PRJ_ID=$(psql_one "SELECT id FROM ops_workspace.project WHERE name = \$\$$name\$\$")
  if [ -n "$PRJ_ID" ]; then ok "project '$name' already present (convergent)"; return 0; fi
  resp=$(curl -s --max-time 30 -X POST "$GW/api/projects" \
    -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$name\",\"description\":\"$desc\",\"visibility\":\"$vis\"}")
  PRJ_ID=$(echo "$resp" | json_get id)
  if [ -n "$PRJ_ID" ]; then ok "created project '$name' ($PRJ_ID)"
  else warn "project create FAILED for '$name': $resp"; FAILED=1; return 1; fi
}

group_project() { # <project_id> <group_name> <sub> <role>: PATCH to group visibility
  local pid="$1" gname="$2" sub="$3" role="$4" gid vis resp
  vis=$(psql_one "SELECT visibility FROM ops_workspace.project WHERE id = '$pid'::uuid")
  [ "$vis" = "group" ] && { ok "already group-visible (convergent)"; return 0; }
  gid=$(psql_one "SELECT id FROM ops_artifact.workshop_group WHERE name = \$\$$gname\$\$")
  [ -n "$gid" ] || { warn "group '$gname' not found (15_workshop_groups_seed)"; FAILED=1; return 1; }
  resp=$(curl -s --max-time 30 -X PATCH "$GW/api/projects/$pid" \
    -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET" \
    -H "Content-Type: application/json" \
    -d "{\"visibility\":\"group\",\"group_id\":\"$gid\"}")
  if [ "$(echo "$resp" | json_get visibility)" = "group" ]; then ok "shared with group '$gname'"
  else warn "group PATCH FAILED: $resp"; FAILED=1; return 1; fi
}

step "project 1/3: EWARS Disease Forecasting (scientist · group)"
ensure_project "EWARS Disease Forecasting" \
  "Climate-driven early-warning forecasting for notifiable diseases (ewars_lite NB2 GLM and friends)." \
  "public" "$SCI_SUB" "$SCI_ROLE" && group_project "$PRJ_ID" "EWARS Working Group" "$SCI_SUB" "$SCI_ROLE"
P1_ID="$PRJ_ID"

step "project 2/3: Supply Chain Analytics (analyst · public)"
# Owned by the analyst: dataset generation runs the governed mart chain and
# R-CONS-SCI is denied marts (workshop-first design), so the public
# supply project belongs to the analyst who can feed it.
ensure_project "Supply Chain Analytics" \
  "Stock positions, stockout forecasting and supply pipeline analytics over HCMIS + mBrana." \
  "public" "$ANA_SUB" "$ANA_ROLE"
P2_ID="$PRJ_ID"

step "project 3/3: Clinical Screening & Triage (analyst · private)"
ensure_project "Clinical Screening & Triage" \
  "Symptom-scorecard screening tools and programme-cascade baselines for triage studies." \
  "private" "$ANA_SUB" "$ANA_ROLE"
P3_ID="$PRJ_ID"

# generated datasets (through the governed /api/datasets/generate chain)
ensure_dataset() { # <title> <template_id> <project_id> <tier> <sub> <role>
  local title="$1" tpl="$2" pid="$3" tier="$4" sub="$5" role="$6" resp aid rows
  aid=$(psql_one "SELECT id FROM ops_artifact.artifact WHERE title = \$\$$title\$\$ AND kind = 'dataset'")
  if [ -n "$aid" ]; then ok "dataset '$title' already present (convergent)"; return 0; fi
  [ -n "$pid" ] || { warn "no project id for dataset '$title'"; FAILED=1; return 1; }
  resp=$(curl -s --max-time 120 -X POST "$GW/api/datasets/generate" \
    -H "X-Mock-User: $sub" -H "X-Mock-Role: $role" -H "X-Mock-Secret: $IMP_SECRET" \
    -H "Content-Type: application/json" \
    -d "{\"title\":\"$title\",\"project_id\":\"$pid\",\"tier\":\"$tier\",\"template_id\":\"$tpl\"}")
  aid=$(echo "$resp" | json_get artifact_id); rows=$(echo "$resp" | json_get rows)
  if [ -n "$aid" ]; then ok "generated '$title' (${rows:-?} rows, $aid)"
  else warn "dataset generate FAILED for '$title': $resp"; FAILED=1; return 1; fi
}

# Datasets are generated by the analyst (R-CONS-ANA): the generator runs the
# governed sql_lab chain and R-CONS-SCI is denied production marts
# by OPA (bundle/roles/r-cons-sci.yaml, workshop-first design).
# The analyst contributes to the EWARS group project via group membership
# (15_workshop_groups_seed.sh puts both principals in the working group).
step "dataset 1/3: Weekly disease surveillance extract (shared · EWARS project)"
ensure_dataset "Weekly disease surveillance extract" "disease-weekly" "$P1_ID" "shared" "$ANA_SUB" "$ANA_ROLE"

step "dataset 2/3: Regional stock position snapshot (public · supply project)"
ensure_dataset "Regional stock position snapshot" "stock-by-region" "$P2_ID" "public" "$ANA_SUB" "$ANA_ROLE"

step "dataset 3/3: Programme cascade baseline (personal · triage project)"
ensure_dataset "Programme cascade baseline" "programme-cascade" "$P3_ID" "personal" "$ANA_SUB" "$ANA_ROLE"

if [ "$FAILED" -ne 0 ]; then echo; echo "== projects/datasets seed FAILED (see ! above) =="; exit 1; fi
echo; echo "== projects/datasets seed complete (3 projects · 3 generated datasets) =="
