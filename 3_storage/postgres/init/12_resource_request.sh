#!/bin/bash
# Workshop resource-request inbox.
#
# A workshop user's "Request bigger pod" click inserts a row here. The Admin
# dashboard's Access Requests page consumes ops_governance.resource_request
# alongside ops_governance.access_request. Approving updates a column the
# JupyterHub spawner reads at next pod start.
#
# Idempotent.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

CREATE TABLE IF NOT EXISTS ops_governance.resource_request (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    requester       uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    requested_at    timestamptz NOT NULL DEFAULT now(),
    -- Spawner profile the user is asking for. Profiles are declared in
    -- 6_access-points/jupyterhub/profiles.yaml; values are slugs from
    -- there (Small/Medium/Large/Custom).
    requested_profile text      NOT NULL,
    requested_cpu     text,        -- "1", "2", "4", "8"  (k8s resource string)
    requested_memory  text,        -- "2Gi", "4Gi", "8Gi", "16Gi"
    requested_gpu     text,        -- NULL for now; "1x-T4" / "1x-A10" later
    justification     text        NOT NULL,
    status            text        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'declined',
                                         'info_requested', 'withdrawn')),
    reviewer          uuid        REFERENCES ops_identity.user (kc_sub),
    decision_at       timestamptz,
    decision_note     text,
    -- Once approved, this profile is what the JupyterHub KubeSpawner
    -- assigns to the next pod the user spawns. NULL until approved.
    granted_profile   text,
    expires_at        timestamptz
);

CREATE INDEX IF NOT EXISTS idx_resource_req_status
    ON ops_governance.resource_request (status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_resource_req_requester
    ON ops_governance.resource_request (requester, requested_at DESC);

COMMENT ON TABLE ops_governance.resource_request IS
    'Workshop user requests for a bigger JupyterHub pod profile. Admin approves; JupyterHub spawner picks up granted_profile on next spawn.';

GRANT SELECT, INSERT, UPDATE ON ops_governance.resource_request
    TO ops_writer, ops_gateway;
GRANT SELECT ON ops_governance.resource_request TO ops_reader;

SQL

echo "[12_resource_request] ops_governance.resource_request ready"
