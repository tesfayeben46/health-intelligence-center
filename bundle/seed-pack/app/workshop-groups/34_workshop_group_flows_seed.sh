#!/bin/bash
# 34_workshop_group_flows_seed.sh: demo content for the invite and request flow,
# so the Community widgets are populated on a fresh install.
# Makes selam.t an owner of the EWARS Working Group, then seeds one pending row for
# each widget: an invitation TO selam.t, a join-request to selam.t's group, a request
# selam.t sent, and an invitation selam.t sent. Idempotent (ON CONFLICT DO NOTHING).
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'
-- Real seeded principals: selam.t=2222…6666, genet.e=8888…cccc, shewaye.a=9999…dddd,
-- steward placeholder=0000…5555. Groups are looked up by name.

-- selam.t manages the EWARS Working Group (so the invite + incoming-request widgets
-- have an owner to act as).
UPDATE ops_artifact.workshop_group_member m
   SET role = 'owner'
  FROM ops_artifact.workshop_group g
 WHERE m.group_id = g.id AND g.name = 'EWARS Working Group'
   AND m.kc_sub = '22222222-3333-4444-5555-666666666666'::uuid;

-- (1) invitation TO selam.t for a group she's not in -> selam.t's inbox
INSERT INTO ops_artifact.workshop_group_invite (group_id, invitee, invited_by, message, status)
SELECT g.id, '22222222-3333-4444-5555-666666666666'::uuid,
       '00000000-0000-0000-0000-000000005555'::uuid,
       'Join us on the surveillance work, your dashboards would be a great fit.', 'pending'
FROM ops_artifact.workshop_group g WHERE g.name = 'EPHI Surveillance'
ON CONFLICT (group_id, invitee) DO NOTHING;

-- (2) join-request from genet.e to selam.t's group -> selam.t's inbox (request)
INSERT INTO ops_artifact.workshop_group_join_request (group_id, requester, message, status)
SELECT g.id, '88888888-9999-aaaa-bbbb-cccccccccccc'::uuid,
       'I work on climate-driven early warning and would love to collaborate.', 'pending'
FROM ops_artifact.workshop_group g WHERE g.name = 'EWARS Working Group'
ON CONFLICT (group_id, requester) DO NOTHING;

-- (3) join-request selam.t sent to another group -> selam.t's "sent"
INSERT INTO ops_artifact.workshop_group_join_request (group_id, requester, message, status)
SELECT g.id, '22222222-3333-4444-5555-666666666666'::uuid,
       'Requesting access to the M&E cascade dashboards.', 'pending'
FROM ops_artifact.workshop_group g WHERE g.name = 'MoH M&E'
ON CONFLICT (group_id, requester) DO NOTHING;

-- (4) invitation selam.t sent (as EWARS owner) -> selam.t's "sent"
INSERT INTO ops_artifact.workshop_group_invite (group_id, invitee, invited_by, message, status)
SELECT g.id, '99999999-aaaa-bbbb-cccc-dddddddddddd'::uuid,
       '22222222-3333-4444-5555-666666666666'::uuid,
       'Would you like to contribute to the early-warning group?', 'pending'
FROM ops_artifact.workshop_group g WHERE g.name = 'EWARS Working Group'
ON CONFLICT (group_id, invitee) DO NOTHING;
SQL
echo "[34_workshop_group_flows_seed] demo invites + join-requests seeded"
