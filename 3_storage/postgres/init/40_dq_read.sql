-- Read path from the DQ governance record to the marts.
--
-- ops_governance.{dq_flag, dq_flag_event, dq_ack, dq_threshold} live in
-- nhdw_ops (written by the gateway detector/ack paths); the governance
-- marts (bi_governance.mart_dq_*) build in nhdw_vault. Same seam as the
-- steward decisions (36_mdm_decisions_read.sql): postgres_fdw foreign
-- tables in nhdw_vault_governance, read-only as ops_reader, so governance
-- KPIs ride the governed mart path (Trino / bi_reader) like every other
-- number. Idempotent. Must run after 36 (foreign server and user mappings)
-- and 38 (the DQ store).
\connect nhdw_vault

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SCHEMA IF NOT EXISTS nhdw_vault_governance;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.foreign_tables
                   WHERE foreign_table_schema = 'nhdw_vault_governance'
                     AND foreign_table_name   = 'dq_flag') THEN
        IMPORT FOREIGN SCHEMA ops_governance
            LIMIT TO (dq_flag, dq_flag_event, dq_ack, dq_threshold)
            FROM SERVER nhdw_ops_ro INTO nhdw_vault_governance;
    END IF;
END $$;

GRANT USAGE ON SCHEMA nhdw_vault_governance TO vault_writer;
GRANT SELECT ON nhdw_vault_governance.dq_flag,
                nhdw_vault_governance.dq_flag_event,
                nhdw_vault_governance.dq_ack,
                nhdw_vault_governance.dq_threshold TO vault_writer;
