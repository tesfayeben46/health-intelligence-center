#!/usr/bin/env bash
# hydrate-data.sh: fetch the deploy inputs that are not in git into a fresh
# checkout, so a from-scratch CI/CD rebuild has everything sources-up.sh and the
# deploy role need. Idempotent and fail-closed. provision.yml calls it after
# actions/checkout and before the ansible run; it can also be run by hand.
#
# What it fetches (the only inputs a fresh `git clone` lacks):
#   1. secrets.yaml   from $NHDW_SECRETS_YAML_B64 (a GitHub Actions secret,
#                     base64 of overlays/dev/secrets.yaml). Never in git.
#   2. source dumps   HCMISWDM.bak and HCMISWCloud.bak from a GitHub Release
#                     ($DATA_RELEASE) via the API and $GH_TOKEN (private repo).
#
# Not fetched here (the deploy reconstructs them):
#   - container images: built from the Dockerfiles in git and pulled from public
#     registries by build-images.sh and cache-images.sh.
#   - LLM weights: pulled from the ollama registry by sources-up.sh.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GH_REPO="${GH_REPO:-national-data-intelligence/national-data-intelligence-platform}"
DATA_RELEASE="${DATA_RELEASE:-data-v1}"
SECRETS_DEST="$REPO_DIR/1_infrastructure/k8s/overlays/dev/secrets.yaml"

log(){ echo "▸ hydrate: $*"; }
fail(){ echo "ERROR: hydrate: $*" >&2; exit 1; }

# 1. secrets.yaml
if [ -n "${NHDW_SECRETS_YAML_B64:-}" ]; then
  mkdir -p "$(dirname "$SECRETS_DEST")"
  echo "$NHDW_SECRETS_YAML_B64" | base64 -d > "$SECRETS_DEST"
  [ -s "$SECRETS_DEST" ] || fail "decoded secrets.yaml is empty (bad NHDW_SECRETS_YAML)"
  log "wrote secrets.yaml ($(wc -c <"$SECRETS_DEST") bytes)"
elif [ -s "$SECRETS_DEST" ]; then
  log "secrets.yaml already present: keeping it"
else
  fail "no secrets.yaml and NHDW_SECRETS_YAML_B64 unset: cannot deploy (fail-closed)"
fi

# 2. source .bak dumps from a GitHub Release
declare -A DUMPS=(
  ["HCMISWDM.bak"]="0_external/sources/HCMISWDM/sample-data/HCMISWDM.bak"
  ["HCMISWCloud.bak"]="0_external/sources/mBrana/sample-data/HCMISWCloud.bak"
)
need=0
for name in "${!DUMPS[@]}"; do [ -s "$REPO_DIR/${DUMPS[$name]}" ] || need=1; done
if [ "$need" = 0 ]; then log "source dumps already present: skipping download"; exit 0; fi

: "${GH_TOKEN:?need GH_TOKEN (repo-scoped) to download release assets from the private repo}"
API="https://api.github.com/repos/$GH_REPO"
rel_json="$(curl -fsSL -H "Authorization: Bearer $GH_TOKEN" \
                 -H "Accept: application/vnd.github+json" \
                 "$API/releases/tags/$DATA_RELEASE")" \
  || fail "release '$DATA_RELEASE' not found: run publish-data-assets.sh first"

for name in "${!DUMPS[@]}"; do
  dest="$REPO_DIR/${DUMPS[$name]}"
  [ -s "$dest" ] && { log "$name present"; continue; }
  aid="$(echo "$rel_json" | jq -r --arg n "$name" '.assets[] | select(.name==$n) | .id')"
  [ -n "$aid" ] && [ "$aid" != "null" ] || fail "asset $name not in release $DATA_RELEASE"
  mkdir -p "$(dirname "$dest")"
  curl -fsSL -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/octet-stream" \
       "$API/releases/assets/$aid" -o "$dest" || fail "download $name failed"
  [ -s "$dest" ] || fail "$name downloaded empty"
  log "downloaded $name -> ${DUMPS[$name]} ($(wc -c <"$dest") bytes)"
done
log "done"
