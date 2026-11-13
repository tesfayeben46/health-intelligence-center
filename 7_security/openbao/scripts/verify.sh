#!/bin/sh
# Confirm OpenBao is initialised, unsealed, with all expected
# mounts + policies + roles in place. Returns 0 on success, 1 on failure.
set -eu
export BAO_ADDR="http://127.0.0.1:8200"
export BAO_TOKEN="$(cat /var/lib/openbao/root-token)"

ok()    { printf "  \033[32mok\033[0m %s\n" "$1"; }
fail()  { printf "  \033[31mFAIL\033[0m %s: %s\n" "$1" "$2"; exit 1; }

echo "Status"
bao status -format=json | grep -q '"sealed": *false' && ok "unsealed" || fail "status" "still sealed"

echo "Mounts"
for m in 'secret/' 'database/'; do
    bao secrets list -format=json | grep -q "\"$m\"" \
        && ok "mount $m" \
        || fail "mount $m" "not present"
done

echo "Policies"
for p in role-r-ops-eng role-r-ops-adm role-r-ops-sec role-r-cons-ana role-r-cons-sci role-r-prod-submit; do
    bao policy list | grep -qx "$p" \
        && ok "policy $p" \
        || fail "policy $p" "not loaded"
done

echo "Database roles (if PG configured)"
if bao read database/config/postgres-vault > /dev/null 2>&1; then
    for r in postgres-vault-writer postgres-vault-bi-reader postgres-vault-landing-writer; do
        bao read database/roles/"$r" > /dev/null 2>&1 \
            && ok "role $r" \
            || fail "role $r" "not present"
    done

    echo "Issue a short-TTL credential (proof DB engine works)"
    bao read -format=json database/creds/postgres-vault-bi-reader > /tmp/cred.json
    user=$(grep -oE '"username":"[^"]+"' /tmp/cred.json | cut -d'"' -f4)
    ttl=$(grep -oE '"lease_duration":[0-9]+' /tmp/cred.json | cut -d: -f2)
    ok "issued bi-reader cred; username=$user, ttl=${ttl}s"
else
    echo "  (skipped: postgres-vault not configured yet)"
fi

echo "Auth methods"
bao auth list -format=json | grep -q '"approle/"' && ok "approle enabled" || fail "approle" "not enabled"

echo "Audit devices"
echo "  (skipped: declarative-only in 2.x)"

echo
echo "All checks passed."
