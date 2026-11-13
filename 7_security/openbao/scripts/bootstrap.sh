#!/bin/sh
# Bootstrap a fresh OpenBao instance:
#   1. Initialise (Shamir 5/3, dev posture)
#   2. Unseal
#   3. Mount kv v2 at secret/
#   4. Mount database secrets engine
#   5. Configure the Postgres (postgres-vault) database connection
#   6. Define short-TTL roles (vault-writer, bi-reader, landing-writer)
#   7. Load every policy under /openbao/policies/ as `role-<id>` policy
#   8. Enable AppRole auth method (for services)
#   9. Audit device: not enabled here (config-driven in OpenBao 2.x)
#  10. Seed in-container secrets into kv
#
# Idempotent: if already initialised, skips re-init and re-uses the
# persisted root token. Re-loads policies (replace-not-append).
#
# Run from inside the container:
#   docker exec openbao /openbao/scripts/bootstrap.sh
set -eu

ROOT_TOKEN_FILE="${ROOT_TOKEN_FILE:-/var/lib/openbao/root-token}"
UNSEAL_KEYS_FILE="${UNSEAL_KEYS_FILE:-/var/lib/openbao/unseal-keys}"

export BAO_ADDR="http://127.0.0.1:8200"

# 1. Initialise
if bao status -format=json 2>/dev/null | grep -q '"initialized": *true'; then
    echo "[bootstrap] already initialised; re-using persisted tokens"
    if [ ! -s "$ROOT_TOKEN_FILE" ] || [ ! -s "$UNSEAL_KEYS_FILE" ]; then
        echo "[bootstrap] FATAL: server is initialised but keys are missing." >&2
        echo "[bootstrap] Wipe /var/lib/openbao/file and re-run." >&2
        exit 1
    fi
else
    echo "[bootstrap] initialising (Shamir 5/3; dev)"
    # Use -format=table because the JSON output is hard to parse without jq.
    # Table format has one key per line: `Unseal Key N: <value>` and
    # `Initial Root Token: <value>`. awk-parseable.
    init_out=$(bao operator init -key-shares=5 -key-threshold=3)
    echo "$init_out" | awk '/^Unseal Key [0-9]+:/ {print $4}' > "$UNSEAL_KEYS_FILE"
    echo "$init_out" | awk '/^Initial Root Token:/ {print $4}' > "$ROOT_TOKEN_FILE"
    chmod 600 "$ROOT_TOKEN_FILE" "$UNSEAL_KEYS_FILE"
    n_keys=$(wc -l < "$UNSEAL_KEYS_FILE")
    n_token=$(wc -c < "$ROOT_TOKEN_FILE")
    if [ "$n_keys" -ne 5 ] || [ "$n_token" -lt 10 ]; then
        echo "[bootstrap] FATAL: parse failed (keys=$n_keys, token_len=$n_token)" >&2
        echo "--- raw init output ---" >&2
        echo "$init_out" >&2
        exit 1
    fi
    echo "[bootstrap] root token + 5 unseal keys persisted (chmod 600)"
fi

# 2. Unseal
if bao status -format=json | grep -q '"sealed": *false'; then
    echo "[bootstrap] already unsealed"
else
    echo "[bootstrap] unsealing"
    head -n 3 "$UNSEAL_KEYS_FILE" | while read -r key; do
        [ -n "$key" ] && bao operator unseal "$key" > /dev/null
    done
fi

export BAO_TOKEN="$(cat "$ROOT_TOKEN_FILE")"
bao status > /dev/null

# 3. KV v2 at secret/
if bao secrets list -format=json | grep -q '"secret/"'; then
    echo "[bootstrap] kv v2 already mounted at secret/"
else
    echo "[bootstrap] mounting kv v2 at secret/"
    bao secrets enable -version=2 -path=secret kv > /dev/null
fi

# 4. Database secrets engine
if bao secrets list -format=json | grep -q '"database/"'; then
    echo "[bootstrap] database engine already mounted"
else
    echo "[bootstrap] mounting database engine"
    bao secrets enable -path=database database > /dev/null
fi

# 5. Configure Postgres connection
# Prefer the password from OpenBao kv (seeded by seed-secrets-to-kv.sh
# after first boot) over the .env value: once seed and rotate have run, the
# .env value is no longer read here. On a fresh first boot kv is empty and
# the .env value is the bootstrap source.
kv_pw=$(curl -sS -H "X-Vault-Token: $BAO_TOKEN" \
    "$BAO_ADDR/v1/secret/data/postgres/vault-admin" 2>/dev/null \
    | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin)["data"]["data"]["password"])
except Exception:
    pass' 2>/dev/null || true)
if [ -n "$kv_pw" ]; then
    echo "[bootstrap] postgres admin pw sourced from secret/postgres/vault-admin"
    PG_VAULT_ADMIN_PASSWORD="$kv_pw"
fi
if [ -n "${PG_VAULT_ADMIN_PASSWORD:-}" ] && \
   [ "${PG_VAULT_ADMIN_PASSWORD}" != "changeme-from-4_storage-postgres-env" ]; then
    echo "[bootstrap] configuring postgres-vault database connection"
    bao write database/config/postgres-vault \
        plugin_name=postgresql-database-plugin \
        allowed_roles="postgres-vault-writer,postgres-vault-bi-reader,postgres-vault-landing-writer" \
        connection_url="postgresql://{{username}}:{{password}}@${PG_VAULT_HOST}:${PG_VAULT_PORT}/${PG_VAULT_DATABASE}?sslmode=disable" \
        username="$PG_VAULT_ADMIN_USER" \
        password="$PG_VAULT_ADMIN_PASSWORD" > /dev/null

    # 6. Roles
    # vault-writer: full DDL/DML on the vault schemas (used by dbt-postgres).
    bao write database/roles/postgres-vault-writer \
        db_name=postgres-vault \
        creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; \
                            GRANT vault_writer TO \"{{name}}\";" \
        default_ttl="1h" max_ttl="4h" > /dev/null

    # bi-reader: inherits bi_reader (read-only; BI consumers).
    bao write database/roles/postgres-vault-bi-reader \
        db_name=postgres-vault \
        creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; \
                            GRANT bi_reader TO \"{{name}}\";" \
        default_ttl="4h" max_ttl="8h" > /dev/null

    # landing-writer: full DDL on landing only (used by the bridge job).
    bao write database/roles/postgres-vault-landing-writer \
        db_name=postgres-vault \
        creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; \
                            GRANT landing_writer TO \"{{name}}\";" \
        default_ttl="1h" max_ttl="4h" > /dev/null

    echo "[bootstrap]   created roles: postgres-vault-{writer,bi-reader,landing-writer}"
else
    echo "[bootstrap] PG_VAULT_ADMIN_PASSWORD not set; skipping DB engine config"
fi

# 7. Load policies
echo "[bootstrap] loading policies"
for f in /openbao/policies/role-*.hcl; do
    name=$(basename "$f" .hcl)
    bao policy write "$name" "$f" > /dev/null
    echo "[bootstrap]   $name"
done

# 8. AppRole auth
if bao auth list -format=json | grep -q '"approle/"'; then
    echo "[bootstrap] approle auth already enabled"
else
    echo "[bootstrap] enabling AppRole auth"
    bao auth enable approle > /dev/null
fi

# 9. File audit device
# Audit devices in OpenBao 2.x are declarative only (config-driven), not
# enabled via the API, so the audit stanza belongs in the server config. No
# audit device is configured here; OpenBao writes operational logs to
# stdout/file per its server config.
echo "[bootstrap] audit device: deferred to A8 (declarative config in 2.x)"

# 10. Seed bootstrap secrets into kv
# Idempotent. Consumers can fetch from secret/<domain>/<name> via kv-get.sh
# after this runs.
#
# Inside the openbao container only PG_VAULT_ADMIN_PASSWORD is in scope
# (the rest live in service-specific .env files the container doesn't
# mount). Follow up by running, from the host:
#
#   bash 7_security/openbao/scripts/seed-secrets-to-kv.sh
#
# which reads every relevant .env file in the repo.
if [ -x /openbao/scripts/seed-secrets-to-kv.sh ]; then
    echo "[bootstrap] seeding inside-container .env vars into kv (F-005, F-011)"
    BAO_TOKEN="$BAO_TOKEN" /openbao/scripts/seed-secrets-to-kv.sh
    echo "[bootstrap] note: run seed-secrets-to-kv.sh from the host to pick up"
    echo "             passwords in keycloak/.env, postgres/.env, etc."
else
    echo "[bootstrap] seed-secrets-to-kv.sh not mounted, skipping"
fi

echo "[bootstrap] DONE"
echo "[bootstrap] root token: $(cat "$ROOT_TOKEN_FILE")  (rotate before non-dev use)"
