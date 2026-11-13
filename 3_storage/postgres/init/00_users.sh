#!/bin/bash
# Renders 00_users.sql.template with env-substituted passwords and pipes it
# into psql. Postgres's docker-entrypoint runs *.sql and *.sh in alphabetical
# order on a fresh data dir.
set -euo pipefail

TEMPLATE=/docker-entrypoint-initdb.d/00_users.sql.template
RENDERED=/tmp/00_users.sql

required_vars=(LANDING_WRITER_PASSWORD VAULT_WRITER_PASSWORD BI_READER_PASSWORD)
for v in "${required_vars[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "[02_users] FATAL: env var $v is empty" >&2
        exit 1
    fi
done

cp "$TEMPLATE" "$RENDERED"
for v in "${required_vars[@]}"; do
    val="${!v}"
    esc=$(printf '%s' "$val" | sed -e 's/[\\&|]/\\&/g')
    sed -i "s|\${$v}|$esc|g" "$RENDERED"
done

# psql is on PATH; the entrypoint exports POSTGRES_USER + a localhost socket.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$RENDERED"

echo "[02_users] provisioned landing_writer, vault_writer, bi_reader"
