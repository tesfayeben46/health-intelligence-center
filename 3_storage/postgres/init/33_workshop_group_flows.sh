#!/bin/bash
# Invitation- and request-gated workshop group membership. A user becomes a
# member only by (a) accepting an invitation from a group owner, or (b) a group
# owner approving the user's join request; there is no self-join. Adds the two
# tables these flows need. Membership itself stays in workshop_group_member.
#
# Idempotent numbered migration (same pattern as 28_dataset_project_link.sh):
# runs at initdb and is re-run against a live pod by
# bundle/scripts/seed-app-security.sh. CREATE IF NOT EXISTS makes it safe to re-run.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- invitations: an owner/manager invites a user INTO their group
-- One live row per (group, invitee); re-inviting refreshes the same row. The
-- invitee accepts (-> becomes a member) or declines; the owner may revoke.
CREATE TABLE IF NOT EXISTS ops_artifact.workshop_group_invite (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    uuid        NOT NULL REFERENCES ops_artifact.workshop_group (id) ON DELETE CASCADE,
    invitee     uuid        NOT NULL REFERENCES ops_identity."user" (kc_sub),
    invited_by  uuid        NOT NULL REFERENCES ops_identity."user" (kc_sub),
    status      text        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'declined', 'revoked')),
    message     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz,
    UNIQUE (group_id, invitee)
);
CREATE INDEX IF NOT EXISTS idx_group_invite_invitee ON ops_artifact.workshop_group_invite (invitee, status);
CREATE INDEX IF NOT EXISTS idx_group_invite_group   ON ops_artifact.workshop_group_invite (group_id, status);

-- join-requests: a user asks to join a group; the owner approves/declines
CREATE TABLE IF NOT EXISTS ops_artifact.workshop_group_join_request (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    uuid        NOT NULL REFERENCES ops_artifact.workshop_group (id) ON DELETE CASCADE,
    requester   uuid        NOT NULL REFERENCES ops_identity."user" (kc_sub),
    status      text        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'declined')),
    message     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz,
    decided_by  uuid        REFERENCES ops_identity."user" (kc_sub),
    UNIQUE (group_id, requester)
);
CREATE INDEX IF NOT EXISTS idx_group_request_requester ON ops_artifact.workshop_group_join_request (requester, status);
CREATE INDEX IF NOT EXISTS idx_group_request_group     ON ops_artifact.workshop_group_join_request (group_id, status);

COMMENT ON TABLE ops_artifact.workshop_group_invite IS
    'Owner-to-user invitations into a workshop group. Accepting inserts a '
    'workshop_group_member row. Membership is invite/request gated (no self-join).';
COMMENT ON TABLE ops_artifact.workshop_group_join_request IS
    'User-to-group join requests. Owner approval inserts a workshop_group_member row.';

-- The gateway connects as ops_gateway; without these it 500s ("permission
-- denied"). Same grant shape as 28_dataset_project_link.sh. Idempotent.
GRANT SELECT, INSERT, UPDATE, DELETE ON ops_artifact.workshop_group_invite       TO ops_gateway, ops_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ops_artifact.workshop_group_join_request TO ops_gateway, ops_writer;
GRANT SELECT ON ops_artifact.workshop_group_invite       TO ops_reader;
GRANT SELECT ON ops_artifact.workshop_group_join_request TO ops_reader;
SQL

echo "[33_workshop_group_flows] invite + join-request tables ensured"
