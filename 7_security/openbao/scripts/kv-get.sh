#!/bin/sh
# Fetch a single field from an OpenBao kv v2 secret.
#
# Usage:
#   kv-get.sh <kv-path> [field]
#
#   kv-get.sh postgres/vault-admin            # prints whole JSON
#   kv-get.sh postgres/vault-admin password   # prints just the password
#
# Consumers that read from kv instead of .env use this pattern:
#
#   export POSTGRES_ADMIN_PASSWORD=$(7_security/openbao/scripts/kv-get.sh \
#       postgres/vault-admin password)
#
# Requires: BAO_ADDR, BAO_TOKEN (or a sufficiently scoped policy token).
set -eu

: "${BAO_ADDR:?BAO_ADDR required (e.g. http://127.0.0.1:8200)}"
: "${BAO_TOKEN:?BAO_TOKEN required}"

KV_PATH="${1:?kv path required, e.g. postgres/vault-admin}"
FIELD="${2:-}"

resp=$(curl -sSf -H "X-Vault-Token: $BAO_TOKEN" \
    "$BAO_ADDR/v1/secret/data/$KV_PATH")

if [ -z "$FIELD" ]; then
    echo "$resp"
    exit 0
fi

# Tiny inline JSON field reader. Avoids a hard jq dep.
echo "$resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)['data']['data']
except Exception:
    sys.exit('kv-get: malformed response; secret may not exist at $KV_PATH')
v = d.get('$FIELD')
if v is None:
    sys.exit('kv-get: field \\'$FIELD\\' not in secret/$KV_PATH (keys: ' + ', '.join(d.keys()) + ')')
print(v)
"
