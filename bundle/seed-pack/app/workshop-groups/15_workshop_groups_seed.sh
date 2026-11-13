#!/bin/bash
# 15_workshop_groups_seed.sh: seed four demo workshop groups so the "shared"
# artifact tier has communities to attach to. The workshop UI does not manage
# membership; these read-only groups make the artifact-tier filter meaningful.
#
# Idempotent (ON CONFLICT DO NOTHING).
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- The owner column FKs to ops_identity.user(kc_sub). Seed a placeholder
-- steward principal so the FK constraint is satisfied without depending
-- on Keycloak being up at init time.
INSERT INTO ops_identity.user (kc_sub, realm, username, role_label)
VALUES ('00000000-0000-0000-0000-000000005555', 'seed', 'demo_steward',
        'R-STEW')
ON CONFLICT (kc_sub) DO NOTHING;

WITH seed(name, description, visibility) AS (
    VALUES
        ('EPHI Surveillance',
         'Ethiopian Public Health Institute · disease surveillance, outbreak detection, EWARS+.',
         'open'),
        ('MoH M&E',
         'Federal Ministry of Health · Monitoring & Evaluation directorate. Cascade indicators, dashboards.',
         'open'),
        ('EWARS Working Group',
         'Cross-org working group on early-warning forecasting (EPHI + MoH + EPSS + academia).',
         'open'),
        ('AAU Research',
         'Addis Ababa University · School of Public Health. Doctoral + postdoc research on Ethiopia health data.',
         'open')
)
INSERT INTO ops_artifact.workshop_group (id, name, description, owner,
                                          visibility, created_at)
SELECT gen_random_uuid(), s.name, s.description,
       '00000000-0000-0000-0000-000000005555'::uuid,
       s.visibility, now()
FROM seed s
LEFT JOIN ops_artifact.workshop_group g ON g.name = s.name
WHERE g.id IS NULL;

-- A handful of synthetic members so the rosters aren't empty. We reuse
-- the existing dev users (the same kc_subs hard-coded in DEV_AUTH).
WITH g AS (
    SELECT id, name FROM ops_artifact.workshop_group
)
INSERT INTO ops_artifact.workshop_group_member (group_id, kc_sub, role, joined_at)
SELECT g.id, m.kc_sub::uuid, m.role, now()
FROM g
JOIN (VALUES
    ('EPHI Surveillance',     'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin'),
    ('EPHI Surveillance',     '11111111-2222-3333-4444-555555555555', 'member'),
    ('MoH M&E',               '11111111-2222-3333-4444-555555555555', 'admin'),
    ('MoH M&E',               'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'member'),
    ('EWARS Working Group',   'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'admin'),
    -- The seeded scientist + analyst (graduation/projects seed principals)
    -- sit in the EWARS working group: the scientist owns the group project,
    -- the analyst generates its shared datasets (R-CONS-SCI is
    -- mart-denied by the workshop-first design, so dataset generation is the
    -- analyst's job).
    ('EWARS Working Group',   '44444444-aaaa-bbbb-cccc-dddddddddddd', 'member'),
    ('EWARS Working Group',   '33333333-aaaa-bbbb-cccc-dddddddddddd', 'member'),
    -- selam.t (the demo analyst login) joins too, so the group project
    -- + its shared datasets are visible on the default demo account.
    ('EWARS Working Group',   '22222222-3333-4444-5555-666666666666', 'member'),
    ('AAU Research',          'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', 'member')
) AS m(group_name, kc_sub, role) ON g.name = m.group_name
ON CONFLICT (group_id, kc_sub) DO NOTHING;

SQL
echo "[15_workshop_groups_seed] demo groups + members seeded"
