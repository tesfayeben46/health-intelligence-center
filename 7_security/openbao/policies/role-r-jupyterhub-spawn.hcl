# OpenBao policy: jupyterhub-spawn
#
# Attached to the long-lived token the Hub uses to mint short-TTL Postgres
# credentials for each notebook container at spawn time. The Hub is not a
# user, so this policy is narrow:
#
#   - read database/creds/postgres-vault-bi-reader  (default)
#   - read database/creds/postgres-vault-writer     (R-CONS-SCI)
#
# Token exchange (Keycloak access token -> OpenBao identity entity -> a
# credential minted under the user identity) would remove this hub-scoped
# token from the path.

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}

path "database/creds/postgres-vault-writer" {
  capabilities = ["read"]
}
