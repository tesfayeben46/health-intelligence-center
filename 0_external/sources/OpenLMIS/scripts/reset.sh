#!/usr/bin/env bash
# Destructive reset of the OpenLMIS v3 reference-distribution containers, then
# re-run restore.sh. Pass -y to skip confirmation.
# No `-v`: the v3 database lives in-container (removed with it anyway), and
# dropping volumes would also wipe hist-db-data and force a re-restore of the
# 7.5M-row dump.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISTRO_DIR="$SRC_DIR/openlmis-ref-distro"

if [ "${1:-}" != "-y" ]; then
  read -r -p "This recreates all OpenLMIS containers and re-seeds demo data (hist-db data survives). Continue? [y/N] " a
  [ "$a" = "y" ] || [ "$a" = "Y" ] || { echo "aborted"; exit 1; }
fi

docker compose -p openlmis \
  --project-directory "$DISTRO_DIR" \
  -f "$DISTRO_DIR/docker-compose.yml" \
  -f "$SRC_DIR/docker-compose.override.yml" down

exec "$SRC_DIR/scripts/restore.sh"
