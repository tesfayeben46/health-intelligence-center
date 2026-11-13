-- Runtime write grants on the permission catalog.
--
-- The admin "Roles and Permissions" editor (gateway routes/perm_admin.py)
-- writes ops_perm.widget, page_widget and role_widget when an admin edits a
-- role's grants. Idempotent and guarded on the role's existence, like the
-- ops_perm grant DO block in 17_perm_schemas.sql.
--
-- ops_perm lives in nhdw_ops, and every init file starts a fresh psql session
-- against POSTGRES_DB (nhdw_vault); a \connect in an earlier file does not
-- carry over. Without this line the GRANTs run against the wrong database and
-- fail with 'schema "ops_perm" does not exist', which aborts postgres init.
\connect nhdw_ops

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT INSERT, UPDATE, DELETE ON ops_perm.widget       TO ops_gateway;
        GRANT INSERT, UPDATE, DELETE ON ops_perm.page_widget  TO ops_gateway;
        GRANT INSERT, UPDATE, DELETE ON ops_perm.role_widget  TO ops_gateway;
    END IF;
END $$;
