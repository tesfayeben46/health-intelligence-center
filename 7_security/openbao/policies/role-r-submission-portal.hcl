# OpenBao policy: submission-portal
#
# The Submission Portal mints short-TTL Postgres credentials for the
# postgres-vault-landing-writer role, which has INSERT/UPDATE on
# nhdw_landing. The portal uses that to:
#   - INSERT new rows into nhdw_landing.submission_inbox
#   - SELECT submitted_by = <current user> rows for the "my submissions" view
#
# Token exchange would let the database audit trail record the signed-in
# producer identity rather than the portal's hub identity.

path "database/creds/postgres-vault-landing-writer" {
  capabilities = ["read"]
}
