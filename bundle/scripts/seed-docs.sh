#!/usr/bin/env bash
# seed-docs.sh: boot step that publishes the curated operator docs and a
# docs-index.json catalogue into the private object-store bucket `nhdw-docs`
# (SeaweedFS). The admin Documentation page reads them through the gateway
# (GET /gw/docs), so an operator can open or download each doc in-app without a
# repo checkout. The bucket is on a persistent volume; re-runs are idempotent.
#
# The boot reads the served docs from bundle/docs/*.md, never from docs/, so no
# seed input lives outside bundle/. sync-docs.sh (run at pack time) keeps those
# copies in step with the authored originals in docs/.
# The store is reached like the other host-side seeds, with the bundled mc
# through a port-forward (bundle/scripts/lib/objstore.sh), rather than a
# configmap, which is limited to 1 MiB. Fails loudly so bootstrap gates on it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
BUCKET=nhdw-docs
DOCS="$REPO/bundle/docs"

step() { echo; echo "▸ $*"; }
ok()   { echo "  ok: $*"; }
warn() { echo "  ! $*" >&2; }

echo "== docs seed (docs/*.md -> object store $BUCKET, served via /gw/docs) =="

# Curated set: file|Title|Section|Description, in display order. Keep Section in
# sync with the groups on the Documentation page.
DOC_ROWS=(
  "architecture.md|Architecture|Overview|The system end to end: source systems -> ingestion -> lake -> warehouse -> the gateway -> the dashboards, running on Kubernetes."
  "deployment.md|Deployment & operations|Operations|Running, rebuilding, tearing down and packing the system; what the repository ships and what it doesn't; the observability stack."
  "security.md|Security & identity|Security|Keycloak realms and the Fayda citizen flow, the role-scoped OPA authorization and row filtering, widget visibility, and single sign-on to the tools."
  "data-and-ml.md|Data, dashboards & ML|Platform|The bi_* marts, the four dashboards and the catalogue-driven widget model, and the machine-learning and assistant layer."
  "data-provenance.md|Data provenance|Reference|Where every dataset comes from: source replicas, generators, restore artifacts distributed separately, direct loads and the time coverage of the snapshot."
  "data-dictionary.md|Data dictionary|Reference|Generated catalogue of every programme, HMIS indicator, supply commodity, region and mart the warehouse carries."
)

# object store (SeaweedFS) via the bundled host-side mc
. "$REPO/bundle/scripts/lib/objstore.sh"
objstore_mc    || { warn "no mc client available: cannot seed docs"; exit 1; }
objstore_creds || { warn "object-store credentials ($OBJSTORE_SECRET) not readable"; exit 1; }
objstore_pf 19003
trap 'objstore_pf_stop' EXIT
objstore_alias && "$MC" --config-dir "$OS_MC_CFG" mb --ignore-existing "L/$BUCKET" >/dev/null 2>&1 \
  || { warn "mc alias/mb failed against $OBJSTORE_SVC"; exit 1; }
ok "bucket $BUCKET ready on $OBJSTORE_SVC"

# build the index on the host, then stream each doc and the index into the
# store with `mc pipe` over stdin (no kubectl cp or tar, no /tmp staging, no size
# limit). Objects are overwritten on each run.
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"; objstore_pf_stop' EXIT
FAILED=0
UP=0
INDEX="$STAGE/docs-index.json"
printf '[\n' > "$INDEX"
first=1
for row in "${DOC_ROWS[@]}"; do
  IFS='|' read -r file title section desc <<< "$row"
  src="$DOCS/$file"
  if [ ! -f "$src" ]; then warn "missing $file: skipped"; FAILED=1; continue; fi
  size=$(wc -c < "$src" | tr -d ' ')
  [ "$first" = 1 ] || printf ',\n' >> "$INDEX"; first=0
  python3 - "$file" "$title" "$section" "$desc" "$size" >> "$INDEX" <<'PY'
import json,sys
f,t,s,d,z=sys.argv[1:6]
sys.stdout.write(json.dumps({"name":f,"title":t,"section":s,"description":d,"size":int(z)}))
PY
  if "$MC" --config-dir "$OS_MC_CFG" pipe "L/$BUCKET/$file" < "$src" >/dev/null 2>&1; then
    ok "uploaded $file"; UP=$((UP+1))
  else
    warn "mc pipe $file failed"; FAILED=1
  fi
done
printf '\n]\n' >> "$INDEX"
"$MC" --config-dir "$OS_MC_CFG" pipe "L/$BUCKET/docs-index.json" < "$INDEX" >/dev/null 2>&1 \
  && ok "uploaded docs-index.json" || { warn "index upload failed"; FAILED=1; }
ok "$BUCKET now holds $UP docs + docs-index.json"

echo
if [ "$FAILED" = 0 ]; then
  echo "== docs seed OK =="
else
  echo "== docs seed had FAILURES ==" >&2; exit 1
fi
