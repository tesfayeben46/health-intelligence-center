#!/usr/bin/env bash
# publish-data-assets.sh: publish the source dumps that are not in git to a
# GitHub Release, so hydrate-data.sh can fetch them on a from-scratch CI/CD
# rebuild. Run once, and again whenever a dump changes, on a machine that has
# the .bak files and a repo-scoped PAT in $GH_TOKEN. Idempotent: replaces an
# existing asset of the same name.
#
#   GH_TOKEN=<repo-scoped PAT> bash bundle/scripts/publish-data-assets.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GH_REPO="${GH_REPO:-national-data-intelligence/national-data-intelligence-platform}"
DATA_RELEASE="${DATA_RELEASE:-data-v1}"
: "${GH_TOKEN:?need a repo-scoped PAT in GH_TOKEN}"
API="https://api.github.com/repos/$GH_REPO"
UP="https://uploads.github.com/repos/$GH_REPO"
auth=(-H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json")

declare -A DUMPS=(
  ["HCMISWDM.bak"]="0_external/sources/HCMISWDM/sample-data/HCMISWDM.bak"
  ["HCMISWCloud.bak"]="0_external/sources/mBrana/sample-data/HCMISWCloud.bak"
)

# ensure the release exists (get-or-create)
rid="$(curl -fsSL "${auth[@]}" "$API/releases/tags/$DATA_RELEASE" 2>/dev/null | jq -r '.id // empty')"
if [ -z "$rid" ]; then
  rid="$(curl -fsSL "${auth[@]}" -X POST "$API/releases" \
        -d "$(jq -n --arg t "$DATA_RELEASE" \
              '{tag_name:$t,name:$t,prerelease:true,
                body:"NHDW not-in-git source dumps for CI hydration (hydrate-data.sh)."}')" \
        | jq -r '.id')"
  echo "created release $DATA_RELEASE (id $rid)"
else
  echo "release $DATA_RELEASE exists (id $rid)"
fi

# upload each dump (replace if present)
for name in "${!DUMPS[@]}"; do
  src="$REPO_DIR/${DUMPS[$name]}"
  [ -s "$src" ] || { echo "skip $name: not present at $src"; continue; }
  old="$(curl -fsSL "${auth[@]}" "$API/releases/$rid/assets" \
         | jq -r --arg n "$name" '.[]|select(.name==$n)|.id')"
  if [ -n "$old" ]; then
    curl -fsSL "${auth[@]}" -X DELETE "$API/releases/assets/$old" && echo "  replaced old $name"
  fi
  curl -fsSL -H "Authorization: Bearer $GH_TOKEN" -H "Content-Type: application/octet-stream" \
       --data-binary @"$src" "$UP/releases/$rid/assets?name=$name" >/dev/null
  echo "  uploaded $name ($(wc -c <"$src") bytes)"
done
echo "done: assets live in release $DATA_RELEASE of $GH_REPO"
