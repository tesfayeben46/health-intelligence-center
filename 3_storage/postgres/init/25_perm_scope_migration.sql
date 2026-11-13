-- Role-level data scope: value scoping as one principal-level,
-- dimension-keyed entitlement.
--
--   1. ops_perm.role_scope: role-level default data scope, keyed by
--      (role_id, dimension, value). Same dimension vocabulary as
--      ops_identity.user_scope.kind, so the resolver (gateway permissions.py)
--      caps filter values at role_scope ∩ user_scope. Seeded by
--      load_security_seed.upsert_role_scope.
--
--   2. Widens ops_identity.user_scope.kind to admit 'artifact_tier' and
--      'workshop_group'. Done here instead of in 05_ops_schemas.sql so 05
--      stays the cold-boot definition and this file the migration overlay.
--
-- Idempotent throughout (IF NOT EXISTS + guarded ALTER + guarded GRANT) so it
-- is safe on a cold-boot init (runs after 17_perm_schemas.sql) and on a warm
-- re-run against an existing cluster.

\connect nhdw_ops


-- role_scope: role-level default data scope
-- role_scope is a positive entitlement: each row is one (dimension, value)
-- the role may see. A dimension with no row is unrestricted (wildcard).
-- managed_by mirrors the provenance pattern: 'seed' rows belong to the YAML
-- loader (add/edit/remove honoured), 'runtime' rows are never touched by it.
CREATE TABLE IF NOT EXISTS ops_perm.role_scope (
    role_id    int  NOT NULL REFERENCES ops_perm.role (id) ON DELETE CASCADE,
    dimension  text NOT NULL CHECK (dimension IN
                  ('geography','programme','facility','artifact_tier','workshop_group')),
    value      text NOT NULL,
    managed_by text NOT NULL DEFAULT 'runtime',
    PRIMARY KEY (role_id, dimension, value)
);
COMMENT ON TABLE ops_perm.role_scope IS
    'Role-level DEFAULT data scope: one (dimension,value) row per thing the role '
    'may see (a dimension with no row = unrestricted). Same dimension vocabulary '
    'as ops_identity.user_scope.kind so role defaults ∪ per-user scope compose in '
    'the resolver (role_scope ∩ user_scope).';


-- Widen user_scope.kind to admit artifact_tier / workshop_group
-- 05_ops_schemas.sql defines the inline CHECK (auto-named user_scope_kind_check)
-- as kind IN (geography, programme, facility). Add the two new dimensions
-- here as a guarded DROP/ADD so warm re-runs never error and 05 stays the
-- cold-boot definition. Guard: only re-create when the current CHECK does not
-- already permit 'artifact_tier'.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'ops_identity.user_scope'::regclass
          AND conname  = 'user_scope_kind_check'
          AND pg_get_constraintdef(oid) LIKE '%artifact_tier%'
    ) THEN
        ALTER TABLE ops_identity.user_scope
            DROP CONSTRAINT IF EXISTS user_scope_kind_check;
        ALTER TABLE ops_identity.user_scope
            ADD CONSTRAINT user_scope_kind_check
            CHECK (kind IN ('geography','programme','facility',
                            'artifact_tier','workshop_group'));
    END IF;
END $$;


-- Grants for the gateway/reader/writer roles
-- Mirrors the guarded grant DO block in 17_perm_schemas.sql. ops_gateway and
-- ops_writer get full CRUD (the admin role editor and the seed loader write
-- it); ops_reader gets SELECT. Granted explicitly so the file is correct when
-- applied standalone.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT USAGE ON SCHEMA ops_perm TO ops_gateway, ops_reader;
        GRANT SELECT ON ops_perm.role_scope TO ops_reader;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_perm.role_scope TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT USAGE ON SCHEMA ops_perm TO ops_writer;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_perm.role_scope TO ops_writer;
    END IF;
END $$;
