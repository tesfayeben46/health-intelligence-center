# OpenBao policy: r-stew-jwt
#
# Attached to per-user tokens issued via jwt-keycloak for R-STEW users.
# Stewards get the analyst reader plus governance read-only on the
# datasets-registry kv space.

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}

# Stewards may inspect every dataset-registry secret entry. Read-only;
# writes go through the governance workflow.
path "secret/data/datasets/*" {
  capabilities = ["read", "list"]
}
