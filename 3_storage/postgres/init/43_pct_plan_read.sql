-- Read path for the PCT plan decision log.
--
-- Same pattern as 36 (MDM decisions) and 41 (ML): the ops decision store is
-- imported read-only into nhdw_vault over the existing nhdw_ops_ro server, so
-- the marts build (bi_supply.mart_pct_plan_decisions) reads approved plans as
-- warehouse facts without a second copy. Never written from the vault side.

\connect nhdw_vault

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SCHEMA IF NOT EXISTS nhdw_vault_plan;
COMMENT ON SCHEMA nhdw_vault_plan IS
    'Read-only plan surface inside nhdw_vault: foreign tables over the nhdw_ops '
    'plan decision log (never written from here).';

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.foreign_tables
                   WHERE foreign_table_schema = 'nhdw_vault_plan'
                     AND foreign_table_name   = 'plan_decision') THEN
        IMPORT FOREIGN SCHEMA ops_plan LIMIT TO (plan_decision)
            FROM SERVER nhdw_ops_ro INTO nhdw_vault_plan;
    END IF;
END $$;

GRANT USAGE ON SCHEMA nhdw_vault_plan TO vault_writer;
GRANT SELECT ON nhdw_vault_plan.plan_decision TO vault_writer;
