#!/bin/bash
# Renders 02_users.sql.template with env-substituted passwords, then pipes it
# into clickhouse-client. CH's docker-entrypoint runs *.sh and *.sql in
# alphabetical order on a fresh data dir.
#
# A wrapper instead of plain .sql because the ClickHouse entrypoint does not
# substitute env vars into .sql files.

set -euo pipefail

TEMPLATE="/docker-entrypoint-initdb.d/02_users.sql.template"
RENDERED="/tmp/02_users.sql"

required_vars=(CLICKHOUSE_STAGING_PASSWORD CLICKHOUSE_MARTS_PASSWORD CLICKHOUSE_READER_PASSWORD)
for v in "${required_vars[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "[02_users] FATAL: env var $v is empty" >&2
        exit 1
    fi
done

# The ClickHouse image has no envsubst (gettext), so substitute with sed.
cp "$TEMPLATE" "$RENDERED"
for v in "${required_vars[@]}"; do
    val="${!v}"
    esc=$(printf '%s' "$val" | sed -e 's/[\\&|]/\\&/g')
    sed -i "s|\${$v}|$esc|g" "$RENDERED"
done

# clickhouse-client is on PATH inside the image. Use the env-provided default
# password (no auth needed for localhost connection during init).
clickhouse-client --multiquery < "$RENDERED"

echo "[02_users] provisioned staging_writer, mart_writer, bi_reader"
