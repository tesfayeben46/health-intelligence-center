-- Read path from model predictions and performance to the marts.
--
-- ops_ml.{ml_predictions, ml_run, model_performance} live in nhdw_ops (written
-- by the ML runner); the PCT forecast marts (bi_supply.mart_pct_forecast*) build
-- in nhdw_vault. Same seam as the steward decisions (36) and the DQ record (40):
-- postgres_fdw foreign tables in nhdw_vault_ml, read-only as ops_reader, so a
-- forecast shown on a dashboard rides the governed mart path (Trino / bi_reader,
-- OPA, masking) instead of a dashboard reading ops_ml directly. Idempotent;
-- must run after 36, which creates the foreign server and user mappings.
\connect nhdw_vault

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SCHEMA IF NOT EXISTS nhdw_vault_ml;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.foreign_tables
                   WHERE foreign_table_schema = 'nhdw_vault_ml'
                     AND foreign_table_name   = 'ml_predictions') THEN
        IMPORT FOREIGN SCHEMA ops_ml
            LIMIT TO (ml_predictions, ml_run, model_performance)
            FROM SERVER nhdw_ops_ro INTO nhdw_vault_ml;
    END IF;
END $$;

GRANT USAGE ON SCHEMA nhdw_vault_ml TO vault_writer;
GRANT SELECT ON nhdw_vault_ml.ml_predictions,
                nhdw_vault_ml.ml_run,
                nhdw_vault_ml.model_performance TO vault_writer;
