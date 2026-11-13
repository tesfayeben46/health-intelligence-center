#!/usr/bin/env bash
# build-spa.sh: build the demo-console SPA (ui/ to dist/) in a throwaway Node
# container, so the host needs no node or npm. Re-run after editing ui/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build -t nhdw-console-spa-build "$HERE" >/dev/null
CID=$(docker create nhdw-console-spa-build)
trap 'docker rm -f "$CID" >/dev/null' EXIT
rm -rf "$HERE/dist"
docker cp "$CID:/dist" "$HERE/dist"
echo "ok: SPA built -> $HERE/dist ($(du -sh "$HERE/dist" | cut -f1))"
