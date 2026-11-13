#!/usr/bin/env bash
# sync-docs.sh: copy the served docs from docs/ into bundle/docs/ so the bundle
# is self-contained (seed-docs.sh reads only bundle/docs/). Docs are authored in
# docs/; pack.sh runs this script so the served copies match. Idempotent.
#
# The served set must match DOC_ROWS in seed-docs.sh.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/docs"
DST="$REPO/bundle/docs"
mkdir -p "$DST"

SERVED=(
  architecture.md deployment.md security.md data-and-ml.md data-provenance.md
  data-dictionary.md
)

# Remove stale served copies first; keep the folder's README.
find "$DST" -maxdepth 1 -name '*.md' ! -name 'README.md' -delete 2>/dev/null || true

n=0
for f in "${SERVED[@]}"; do
  if [ -f "$SRC/$f" ]; then
    cp -f "$SRC/$f" "$DST/$f"; n=$((n + 1))
  else
    echo "  ! docs/$f missing: not synced to bundle" >&2
  fi
done
echo "ok: sync-docs: $n served docs synced docs/ -> bundle/docs/"
