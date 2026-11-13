#!/bin/bash
# Runs the Python loader 7_security/scripts/load_security_seed.py to populate
# ops_perm.* and the ops_identity additions from the YAML in
# bundle/seed-pack/app/security/.
#
# Runs as an init script after 17_perm_schemas.sql has created the tables
# (local container; the Kubernetes initdb ConfigMap omits it and the seeding
# step runs the loader as a Job). Idempotent (the loader uses ON CONFLICT DO UPDATE).
# Skips with a warning when the seed dir or loader is not mounted.
#
# Installs python3 (apt) and PyYAML/psycopg (pip, system-wide) when the image
# lacks them.
#

set -euo pipefail

SEED_DIR="${NHDW_SEED_DIR:-/opt/seeds/security}"
LOADER="${NHDW_LOADER:-/opt/scripts/load_security_seed.py}"
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

if [ ! -d "$SEED_DIR" ]; then
    echo "[18_security_seed] WARN: seed dir not present at $SEED_DIR; skipping"
    echo "[18_security_seed] (mount bundle/seed-pack/app/security/ to /opt/seeds/security in compose)"
    exit 0
fi

if [ ! -f "$LOADER" ]; then
    echo "[18_security_seed] WARN: loader not found at $LOADER; skipping"
    exit 0
fi

# Ensure python3 + PyYAML + psycopg are present.
if ! command -v python3 >/dev/null 2>&1; then
    echo "[18_security_seed] installing python3 + pip via apt-get …"
    apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3 python3-pip python3-venv >/dev/null
fi
if ! python3 -c "import yaml, psycopg" 2>/dev/null; then
    echo "[18_security_seed] installing PyYAML + psycopg via pip …"
    python3 -m pip install --quiet --break-system-packages PyYAML 'psycopg[binary]'
fi

export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export OPS_DB_NAME="$OPS_DB"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

echo "[18_security_seed] loading from $SEED_DIR into db=$OPS_DB"
python3 "$LOADER" --seed-dir "$SEED_DIR" --pg-db "$OPS_DB"
echo "[18_security_seed] seeded"
