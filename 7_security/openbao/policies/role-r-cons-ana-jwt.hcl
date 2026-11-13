# OpenBao policy: r-cons-ana-jwt
#
# Attached to per-user tokens issued via the jwt-keycloak auth method
# when the signed-in user carries the R-CONS-ANA realm role. Lets the
# user mint short-TTL Postgres bi_reader credentials for their own
# session, so DB credential audit lands under the user identity (entity
# alias), not a hub service identity.
#
# Read-only by design: analyst users never write to vault objects. Writer
# credentials for R-CONS-SCI belong in a separate policy.

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}

# Allow the user to self-introspect their token (debugging + UI display).
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
