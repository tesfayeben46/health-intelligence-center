#!/bin/bash
# Static-secret rotation for the ClickHouse `staging_writer` user.
#
# OpenBao 2.4.3 ships no clickhouse-database-plugin, so short-TTL ClickHouse
# users cannot be minted dynamically. This approximates dynamic credentials by
# rotating the static password on a cadence and storing the current value in
# OpenBao KV.
#
# Producers (the staging dbt build) fetch the current password from KV at
# task start. Run this script periodically (manually, from cron on the
# scheduler host, or from a rotation DAG).
#
# Side effects:
#   - ALTER USER staging_writer IDENTIFIED WITH plaintext_password BY '<new>'
#   - bao kv put secret/clickhouse/staging-writer password=<new> rotated_at=<utc>
#
# Idempotent (each invocation rotates).
#
# Requires:
#   - $OPENBAO_ADDR, $OPENBAO_TOKEN (root or a policy with kv put on the path)
#   - Default user reachable in the `clickhouse` container; CH default user
#     has ACCESS MANAGEMENT (set at container init).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
OPENBAO_ADDR="${OPENBAO_ADDR:-http://127.0.0.1:8200}"
: "${OPENBAO_TOKEN:?OPENBAO_TOKEN must be set (root or a token with kv put on secret/clickhouse/*)}"

USERNAME=staging_writer
NEW_PW=$(openssl rand -hex 24)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "rotating ClickHouse user '$USERNAME' at $NOW"

# 1. ALTER USER on the live CH server (default user has ACCESS MANAGEMENT).
docker exec clickhouse clickhouse-client --user default \
    --query "ALTER USER ${USERNAME} IDENTIFIED WITH plaintext_password BY '${NEW_PW}'"

# 2. Write to KV (v2 layout: data wrapped in {"data": {...}}).
HTTP=$(curl -sS -o /tmp/.bao_rot_resp -w '%{http_code}' \
    -X POST \
    -H "X-Vault-Token: $OPENBAO_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"data\":{\"password\":\"$NEW_PW\",\"rotated_at\":\"$NOW\"}}" \
    "$OPENBAO_ADDR/v1/secret/data/clickhouse/staging-writer")

case "$HTTP" in
    200|204)
        echo "wrote new password to OpenBao KV (HTTP $HTTP)"
        ;;
    *)
        echo "KV write failed HTTP $HTTP; $(cat /tmp/.bao_rot_resp)" >&2
        echo "CH password WAS rotated; system is now in INCONSISTENT state" >&2
        exit 1
        ;;
esac
rm -f /tmp/.bao_rot_resp

# 3. Smoke-check: log in with the new pw.
docker exec clickhouse clickhouse-client --user "$USERNAME" --password "$NEW_PW" \
    --query "SELECT currentUser() AS user, '$NOW' AS rotated_at" \
    || { echo "post-rotate smoke test FAILED" >&2; exit 1; }

echo "rotation complete"
