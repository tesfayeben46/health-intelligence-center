#!/usr/bin/env bash
# run.sh: start the NHDW Demo Console on the host (python3 standard library only).
#   ./run.sh            serves http://localhost:8500/ (demo catalog and system console)
# Env: CONSOLE_PORT (default 8500), GATEWAY_URL, DEMO_SCENARIOS_PATH.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo "ERROR: python3 is required"; exit 1; }
[ -d "$HERE/dist" ] || bash "$HERE/build-spa.sh"
exec python3 "$HERE/console.py" "$@"
