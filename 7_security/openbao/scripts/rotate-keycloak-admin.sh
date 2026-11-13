#!/bin/sh
# Rotate Keycloak master-realm admin password.
#
# Issues a new random password, calls Keycloak Admin REST API to update,
# then writes the new value to OpenBao kv at secret/services/keycloak.
# Same pattern as rotate-postgres-fhir-writer.sh and
# rotate-clickhouse-staging-writer.sh, for the Keycloak admin user.
set -eu
KC_HOST="${KC_HOST:-http://127.0.0.1:8090}"
KC_USER="${KC_USER:-keycloak_admin}"
KC_OLD_PASSWORD="${KC_OLD_PASSWORD:?need current password to rotate}"
export BAO_ADDR="${BAO_ADDR:-http://127.0.0.1:8200}"
export BAO_TOKEN="${BAO_TOKEN:-$(cat /var/lib/openbao/root-token)}"

NEW=$(openssl rand -hex 24)

echo '[rotate-kc] obtaining admin access token'
TOKEN=$(curl -s -X POST "$KC_HOST/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password" -d "client_id=admin-cli" \
    -d "username=$KC_USER" -d "password=$KC_OLD_PASSWORD" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

ADMIN_ID=$(curl -s "$KC_HOST/admin/realms/master/users?username=$KC_USER" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

echo '[rotate-kc] setting new password'
curl -s -X PUT "$KC_HOST/admin/realms/master/users/$ADMIN_ID/reset-password" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"type\":\"password\",\"value\":\"$NEW\",\"temporary\":false}" \
    && echo '  KC accepted new password'

echo '[rotate-kc] writing to OpenBao kv'
bao kv patch secret/services/keycloak bootstrap_admin_password="$NEW" > /dev/null
echo "[rotate-kc] done: new password lives at secret/services/keycloak::bootstrap_admin_password"
