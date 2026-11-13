# OpenBao policy: internal-bi
#
# Attached to the long-lived token the Internal BI dashboard uses to mint
# short-TTL Postgres credentials for the bi_* marts.
#
# Scope:
#   - read on database/creds/postgres-vault-bi-reader
#
# The bi_reader role (3_storage/postgres/init/01_schemas.sql) is read-only:
# SELECT on the published bi_* schemas and the business vault, nothing on
# landing or the raw vault. Token exchange would let the credential audit
# record the signed-in user instead of the dashboard's hub identity.

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}
