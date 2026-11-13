#!/bin/bash
# Adds ops_workspace.project, the workshop "project" entity (a collaborator's
# unit of work). Sharing is controlled by a single visibility tag:
# private | group | public.
#
# A separate numbered migration instead of an edit to 05_ops_schemas.sql:
# 05 runs only at initdb time on an empty data dir, while this idempotent
# file also brings an existing cluster up to date when executed in the
# postgres-vault pod (initdb never re-runs). Same pattern as 13_graduation.sh.
#
# The gateway's POST /api/projects also provisions an object-store prefix
# projects/<id>/files/ and an MLflow experiment /projects/<id>; those live in
# the object store and MLflow, not here.
#
# Idempotent (CREATE ... IF NOT EXISTS + explicit grants; project rows are
# user-created, never seeded).
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

CREATE TABLE IF NOT EXISTS ops_workspace.project (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          text        NOT NULL UNIQUE,                 -- kebab(name)+short-id; stable public handle
    name          text        NOT NULL,
    owner_kc_sub  uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    visibility    text        NOT NULL DEFAULT 'private'
                  CHECK (visibility IN ('private', 'group', 'public')),
    group_id      uuid        REFERENCES ops_artifact.workshop_group (id),
    status        text        NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'archived')),
    description   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    -- The ONE visibility tag decides who sees it (B4). group_id is required
    -- iff visibility='group': enforced here as a true invariant, and again
    -- at the gateway (PATCH). Non-group projects never carry a stray group.
    CONSTRAINT project_group_iff_group_visibility CHECK (
        (visibility =  'group' AND group_id IS NOT NULL) OR
        (visibility <> 'group' AND group_id IS NULL)
    )
);
COMMENT ON TABLE ops_workspace.project IS
    'B2/B4 workshop project: a researcher''s unit of work. Owner + ONE '
    'visibility tag (private|group|public) that decides who sees it. Files '
    'live under MinIO projects/<id>/files/; runs under MLflow experiment '
    '/projects/<id>. Fork + fine-grained project_share grants are deferred.';
COMMENT ON COLUMN ops_workspace.project.visibility IS
    'private = owner only · group = members of group_id · public = any '
    'authenticated user. The single sharing control (B4 sharing v1).';

CREATE INDEX IF NOT EXISTS idx_project_owner      ON ops_workspace.project (owner_kc_sub);
CREATE INDEX IF NOT EXISTS idx_project_visibility ON ops_workspace.project (visibility);
CREATE INDEX IF NOT EXISTS idx_project_group      ON ops_workspace.project (group_id)
    WHERE group_id IS NOT NULL;
-- Trigram index for the catalog's ?q= name search (pg_trgm created in 05).
CREATE INDEX IF NOT EXISTS idx_project_name_trgm  ON ops_workspace.project
    USING gin (name gin_trgm_ops);

-- Touch updated_at on mutation (same helper the other ops_workspace tables use).
DROP TRIGGER IF EXISTS trg_project_touch ON ops_workspace.project;
CREATE TRIGGER trg_project_touch BEFORE UPDATE ON ops_workspace.project
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();

-- Grants: this table is created by the superuser (not ops_writer), so the
-- ALTER DEFAULT PRIVILEGES in 06 don't cover it, so grant explicitly, exactly
-- like 13_graduation.sh. The gateway needs SELECT/INSERT/UPDATE (list/detail/
-- create/patch); there is no DELETE route (archive via status), so no DELETE.
GRANT SELECT, INSERT, UPDATE ON ops_workspace.project TO ops_writer, ops_gateway;
GRANT SELECT ON ops_workspace.project TO ops_reader;
SQL

echo "[22_project] ops_workspace.project ready"
