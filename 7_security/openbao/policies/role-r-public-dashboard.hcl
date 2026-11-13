# OpenBao policy: public-dashboard
#
# Attached to the long-lived token the Public Dashboard uses to mint
# short-TTL read-only Postgres credentials for the bi_public.* marts.
#
# Scope:
#   - read on database/creds/postgres-vault-bi-reader
#
# The bi_reader role (3_storage/postgres/init/01_schemas.sql) is read-only:
# SELECT on the published bi_* schemas and the business vault, nothing on
# landing or the raw vault. A leaked token therefore exposes every mart
# bi_reader can read, not only bi_public.
#
# Token exchange would let the dashboard's per-request audit land under
# "user=anonymous role=R-PUB-ANON" inside the database, not only in the app's
# Loki stream.

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}
