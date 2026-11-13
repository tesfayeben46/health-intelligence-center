# R-OPS-ENG: Data Engineer / Platform Operator.
#
# Can issue short-TTL DB creds for analytics-side access (vault_writer +
# staging_writer roles), can read service KV secrets, can list audit
# device status, can read system health. Cannot read root-token-equivalent
# paths. Cannot modify policies.

# KV: read service secrets (passwords / API tokens needed to operate the platform).
path "secret/data/services/*" {
  capabilities = ["read", "list"]
}
path "secret/metadata/services/*" {
  capabilities = ["read", "list"]
}

# Database secrets engine: engineer-facing roles only (no PHI-reader role).
path "database/creds/postgres-vault-writer" {
  capabilities = ["read"]
}
path "database/creds/clickhouse-staging-writer" {
  capabilities = ["read"]
}

# Sys metadata (health, mount listing) for the ops hub.
path "sys/health" {
  capabilities = ["read"]
}
path "sys/mounts" {
  capabilities = ["read"]
}

# Self-token introspection; every authenticated identity needs this.
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
