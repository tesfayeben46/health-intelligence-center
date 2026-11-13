-- Normalized, reusable data-access permissions.
--
-- Each bundle/roles/r-*.yaml declares its own permission tuples, and
-- 7_security/opa/scripts/compile.py emits one `allow` rule per tuple. Here a
-- `permission` is the reusable unit (one row per distinct allow-rule tuple)
-- and `role_permission` the M:N link to roles. The tuple maps 1:1 onto what
-- compile.py consumes, so policy semantics do not change: the seed populates
-- these tables from the role YAMLs (deduplicated), `compile.py --from-db`
-- reads them back, and its rego must match the YAML-sourced rego byte for byte
-- (equivalence check). Runs after 17_perm_schemas.sql (ops_perm.role).
--
-- ops_perm lives in nhdw_ops, and each init file starts a fresh psql session
-- against POSTGRES_DB (nhdw_vault); an earlier file's \connect does not carry
-- over. Without this the CREATE TABLEs land in the wrong database.
\connect nhdw_ops
-- Idempotent.

-- permission: one row per distinct allow-rule tuple
CREATE TABLE IF NOT EXISTS ops_perm.permission (
    id            serial      PRIMARY KEY,
    code          text        UNIQUE,                       -- optional human handle, e.g. 'prg-agg-read-cat'
    subject       text        NOT NULL CHECK (subject IN ('PT','HP','FAC','PRD','PRG')),
    members       text        NOT NULL DEFAULT 'scoped' CHECK (members IN ('all','scoped','self')),
    granularity   text        NOT NULL CHECK (granularity IN ('raw','aggregate','deidentified','metadata')),
    verbs         text[]      NOT NULL,                      -- ['read','export','schedule',...]
    purposes      text[]      NOT NULL DEFAULT '{public-health}',
    data_classes  text[]      NOT NULL DEFAULT '{}',         -- empty = any (matches current YAML data_class:[])
    sensitivity   text[]      NOT NULL DEFAULT '{}',         -- empty = default only
    description   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    -- A permission's identity is the whole tuple, which makes the seed dedup
    -- idempotent: a tuple shared by 3 roles inserts once, then 3 role_permission links.
    CONSTRAINT uq_permission_tuple
        UNIQUE (subject, members, granularity, verbs, purposes, data_classes, sensitivity)
);
COMMENT ON TABLE ops_perm.permission IS
    'Reusable data-access permission = one OPA allow-rule tuple (subject/members/'
    'granularity/verbs/purposes/data_classes/sensitivity). compile.py sources these '
    'from the DB to emit per-role rego; the tuple UNIQUE dedups shared permissions.';

-- Heal the granularity domain if the table pre-exists with an older CHECK
-- (CREATE TABLE IF NOT EXISTS won't alter an existing constraint). Idempotent.
ALTER TABLE ops_perm.permission DROP CONSTRAINT IF EXISTS permission_granularity_check;
ALTER TABLE ops_perm.permission ADD  CONSTRAINT permission_granularity_check
    CHECK (granularity IN ('raw','aggregate','deidentified','metadata'));

-- role_permission: M:N (a permission may be granted to many roles)
CREATE TABLE IF NOT EXISTS ops_perm.role_permission (
    role_id       int  NOT NULL REFERENCES ops_perm.role (id)       ON DELETE CASCADE,
    permission_id int  NOT NULL REFERENCES ops_perm.permission (id) ON DELETE CASCADE,
    -- Preserves the role's YAML declaration order so compile.py re-emits the
    -- in-tree policies/*.rego byte for byte (the equivalence check). The allow
    -- rules are order-independent; keeping order makes the DB-sourced output
    -- diff-free and checkable offline.
    ordering      int  NOT NULL DEFAULT 0,
    -- 'seed' rows are re-derived from the role YAMLs each boot; 'runtime' rows are
    -- created by an admin via perm_admin and must survive re-seed (same convention
    -- as ops_perm.role_scope).
    managed_by    text NOT NULL DEFAULT 'seed' CHECK (managed_by IN ('seed','runtime')),
    granted_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX IF NOT EXISTS ix_role_permission_permission ON ops_perm.role_permission (permission_id);
COMMENT ON TABLE ops_perm.role_permission IS
    'Grants a reusable ops_perm.permission to a role (M:N). One permission row -> '
    'many roles, eliminating the per-role tuple duplication.';

-- runtime write-grants for the admin editor (perm_admin.py)
-- Same guarded DO-block pattern as 23_perm_admin_grants.sql. The seed loader
-- connects as the DB superuser, so it needs no grant; only the gateway's runtime
-- permission CRUD does.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_perm.permission      TO ops_gateway;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_perm.role_permission TO ops_gateway;
        GRANT USAGE, SELECT ON SEQUENCE ops_perm.permission_id_seq       TO ops_gateway;
    END IF;
END $$;
