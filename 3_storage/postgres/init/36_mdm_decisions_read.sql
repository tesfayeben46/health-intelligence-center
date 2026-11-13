-- Read path from steward MDM decisions to the vault build.
--
-- ops_governance.mdm_match_decision lives in nhdw_ops (the steward console
-- writes it through the gateway); dbt-vault builds in nhdw_vault. Both
-- databases share one Postgres instance, so a postgres_fdw foreign table over
-- the pod-local socket (trusted per pg_hba) exposes the decision log
-- read-only in nhdw_vault: no credentials to manage, no copy step to drift,
-- and v_facility_resolution can LEFT JOIN it at build time.
--
-- Connects as ops_reader (SELECT-only on ops_governance), so a compromised
-- vault role still cannot write decisions.
--
-- IMPORT FOREIGN SCHEMA snapshots the remote column list. If 35_mdm.sql
-- changes mdm_match_decision's columns, refresh with:
--   DROP FOREIGN TABLE nhdw_vault_governance.mdm_match_decision;
-- and re-apply this file.

CREATE EXTENSION IF NOT EXISTS postgres_fdw;

CREATE SCHEMA IF NOT EXISTS nhdw_vault_governance;
COMMENT ON SCHEMA nhdw_vault_governance IS
    'Read-only governance surface inside nhdw_vault: foreign tables over the '
    'nhdw_ops decision stores the vault build consumes (never writes).';

CREATE SERVER IF NOT EXISTS nhdw_ops_ro
    FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host '/var/run/postgresql', dbname 'nhdw_ops');

CREATE USER MAPPING IF NOT EXISTS FOR vault_admin
    SERVER nhdw_ops_ro OPTIONS ("user" 'ops_reader');
-- password_required 'false' (superuser-set) is required for vault_writer:
-- postgres_fdw refuses passwordless connections for non-superusers by
-- default, even over the trusted local socket, and the dbt build (which runs
-- as vault_writer) would fail at the first decision-log read.
CREATE USER MAPPING IF NOT EXISTS FOR vault_writer
    SERVER nhdw_ops_ro OPTIONS ("user" 'ops_reader', password_required 'false');

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.foreign_tables
                   WHERE foreign_table_schema = 'nhdw_vault_governance'
                     AND foreign_table_name   = 'mdm_match_decision') THEN
        IMPORT FOREIGN SCHEMA ops_governance LIMIT TO (mdm_match_decision)
            FROM SERVER nhdw_ops_ro INTO nhdw_vault_governance;
    END IF;
END $$;

GRANT USAGE ON SCHEMA nhdw_vault_governance TO vault_writer;
GRANT SELECT ON nhdw_vault_governance.mdm_match_decision TO vault_writer;
