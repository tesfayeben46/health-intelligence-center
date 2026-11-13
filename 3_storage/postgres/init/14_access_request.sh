#!/bin/bash
# Extends ops_governance.access_request with the columns the public
# dashboard's "Request access" form fills in. The base table (id, requester,
# requested_role, status, ...) is created in 05_ops_schemas.sql; this script
# adds the public-form columns and a
# tracking-ID counter so anonymous submissions get an AR-YYYY-NNNN code.
#
# Idempotent (ADD COLUMN IF NOT EXISTS).
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- The base table created earlier expected an authenticated requester
-- (FK to ops_identity.user.kc_sub) plus requested_role + justification.
-- Public-form submissions don't preselect a role and use `purpose`
-- instead of `justification`, so loosen all three.
ALTER TABLE ops_governance.access_request
    ALTER COLUMN requester      DROP NOT NULL,
    ALTER COLUMN requested_role DROP NOT NULL,
    ALTER COLUMN justification  DROP NOT NULL;

-- Public-form columns.
ALTER TABLE ops_governance.access_request
    ADD COLUMN IF NOT EXISTS tracking_id      text,
    ADD COLUMN IF NOT EXISTS submitted_at     timestamptz,
    ADD COLUMN IF NOT EXISTS full_name        text,
    ADD COLUMN IF NOT EXISTS email            text,
    ADD COLUMN IF NOT EXISTS phone            text,
    ADD COLUMN IF NOT EXISTS org              text,
    ADD COLUMN IF NOT EXISTS role             text,
    ADD COLUMN IF NOT EXISTS supervisor_email text,
    ADD COLUMN IF NOT EXISTS irb              text,
    ADD COLUMN IF NOT EXISTS dashboards       text[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS purpose          text,
    ADD COLUMN IF NOT EXISTS granted_role     text,
    ADD COLUMN IF NOT EXISTS granted_scope    jsonb  DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS client_ip        text,
    ADD COLUMN IF NOT EXISTS user_agent       text,
    -- §4.8 provisioning: on approve the gateway creates the Keycloak account,
    -- assigns the role + scope, and emails a set-password link. These track it.
    ADD COLUMN IF NOT EXISTS provisioning_status text DEFAULT 'not_provisioned',
    ADD COLUMN IF NOT EXISTS kc_user_id          text;

-- Default submitted_at from existing created_at for legacy rows.
UPDATE ops_governance.access_request
   SET submitted_at = created_at
 WHERE submitted_at IS NULL AND created_at IS NOT NULL;

ALTER TABLE ops_governance.access_request
    ALTER COLUMN submitted_at SET DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_access_req_tracking_id
    ON ops_governance.access_request (tracking_id)
    WHERE tracking_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_access_req_status
    ON ops_governance.access_request (status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_access_req_email
    ON ops_governance.access_request (lower(email));

COMMENT ON TABLE ops_governance.access_request IS
    'Dashboard access-request queue. Two callers: (a) authenticated users '
    'requesting a wider role/scope (requester is set), (b) anonymous public '
    'submissions from the "Request access" form (full_name + email set, '
    'requester NULL). Reviewed via admin dashboard.';

-- Tracking-ID counter so AR-YYYY-NNNN is monotonic per year.
CREATE TABLE IF NOT EXISTS ops_governance._access_request_counter (
    year_id integer PRIMARY KEY,
    next_n  integer NOT NULL DEFAULT 1
);

CREATE OR REPLACE FUNCTION ops_governance.next_access_request_tracking_id(
    year_in integer DEFAULT EXTRACT(YEAR FROM now())::integer)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    INSERT INTO ops_governance._access_request_counter(year_id, next_n)
        VALUES (year_in, 1)
        ON CONFLICT (year_id) DO NOTHING;
    UPDATE ops_governance._access_request_counter
       SET next_n = next_n + 1
     WHERE year_id = year_in
    RETURNING next_n - 1 INTO n;
    RETURN format('AR-%s-%s', year_in, lpad(n::text, 4, '0'));
END $$;

GRANT EXECUTE ON FUNCTION ops_governance.next_access_request_tracking_id(integer)
    TO ops_writer, ops_gateway;
GRANT SELECT, INSERT, UPDATE ON ops_governance._access_request_counter
    TO ops_writer, ops_gateway;
SQL

echo "[14_access_request] access_request extended + tracking-ID counter ready"
