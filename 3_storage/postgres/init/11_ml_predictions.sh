#!/bin/bash
# The `ops_ml` schema in nhdw_ops: the ML tier's operational store.
#
# Tables:
#   ml_run             one row per runner invocation (train / batch / ad-hoc):
#                      lifecycle status, row_count, latency, MLflow linkage.
#   ml_predictions     the unified flat forecast store. One row per
#                      (run, model, disease, district, ISO year/week).
#                      Every batch model (ears, outbreak, seasonal, ewars_lite,
#                      stockout_forecast) emits this shape; model-specific
#                      extras go in `attrs` jsonb.
#   model_deployment   scheduling registry, one row per model. Scheduling a
#                      model means an INSERT/UPDATE here; the weekly Airflow
#                      DAG iterates enabled batch rows.
#   model_drift        per-run feature drift against the training reference
#                      profile (PSI/KS), written by the drift check.
#   model_performance  windowed accuracy metrics against mart actuals.
#
# Writer idempotency contract: a prediction writer does
#     DELETE FROM ops_ml.ml_predictions WHERE run_id = $1;  then INSERTs
# so re-running a batch is safe. Writers therefore get DELETE here (ops_ml is
# machine-managed, run-scoped state, unlike append-only ops_audit).
#
# Latest-run read pattern (dashboards/gateway):
#     run_id = (SELECT id FROM ops_ml.ml_run
#               WHERE model_name = $1 AND status = 'success'
#               ORDER BY finished_at DESC LIMIT 1)
#
# Idempotent. An older jsonb-shaped ml_predictions table is dropped only if
# empty; a populated one aborts the script.
set -euo pipefail

OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

CREATE SCHEMA IF NOT EXISTS ops_ml;
COMMENT ON SCHEMA ops_ml IS
    'ML operational store: flat forecast rows, run log, deployment registry, drift + performance monitoring.';

-- Legacy-shape guard
-- The earlier ml_predictions was jsonb-output-shaped (geography_id/key/
-- output columns). The flat schema replaces it. Only an empty legacy table
-- may be dropped; data would mean a real migration, so fail loudly instead.
DO $$
DECLARE
    n bigint;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'ops_ml' AND table_name = 'ml_predictions')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'ops_ml' AND table_name = 'ml_predictions'
                         AND column_name = 'district')
    THEN
        EXECUTE 'SELECT count(*) FROM ops_ml.ml_predictions' INTO n;
        IF n > 0 THEN
            RAISE EXCEPTION 'ops_ml.ml_predictions has the legacy jsonb shape WITH % rows; refusing to drop; migrate the rows manually first', n;
        END IF;
        DROP TABLE ops_ml.ml_predictions;
        RAISE NOTICE 'ops_ml.ml_predictions: dropped empty legacy jsonb-shaped table';
    END IF;
END$$;

-- Per-run log (one row per runner invocation)
CREATE TABLE IF NOT EXISTS ops_ml.ml_run (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name      text        NOT NULL,
    model_version   text        NOT NULL,
    deployment_kind text        NOT NULL CHECK (deployment_kind IN ('batch', 'on_demand')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'success', 'failed')),
    row_count       bigint,
    latency_ms      integer,
    triggered_by    uuid        REFERENCES ops_identity.user (kc_sub),
    trigger_kind    text        NOT NULL CHECK (trigger_kind IN ('schedule','manual','api')),
    input_filters   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error_msg       text,
    -- MLflow linkage (added with the flat-forecast rework):
    run_label             text,   -- human label, e.g. 'seed-train' / 'weekly-batch'
    mlflow_run_id         text,   -- MLflow run this row corresponds to (train runs)
    reference_profile_uri text    -- artifact URI of the training feature profile (drift baseline)
);
-- Upgrade path for databases initialised before the rework:
ALTER TABLE ops_ml.ml_run ADD COLUMN IF NOT EXISTS run_label             text;
ALTER TABLE ops_ml.ml_run ADD COLUMN IF NOT EXISTS mlflow_run_id         text;
ALTER TABLE ops_ml.ml_run ADD COLUMN IF NOT EXISTS reference_profile_uri text;

CREATE INDEX IF NOT EXISTS idx_ml_run_model  ON ops_ml.ml_run (model_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ml_run_status ON ops_ml.ml_run (status, started_at DESC);

-- Predictions: unified flat forecast schema
-- disease: disease_code for the disease family; '' (default) for models that
-- don't bind to a disease (e.g. stockout_forecast) so the 6-col PK works.
-- district: geography id (woreda_uid for the disease family, commodity name
-- for stockout_forecast). year/week: ISO year/week of the target period.
CREATE TABLE IF NOT EXISTS ops_ml.ml_predictions (
    run_id            uuid        NOT NULL REFERENCES ops_ml.ml_run (id) ON DELETE CASCADE,
    model_name        text        NOT NULL,
    model_version     text        NOT NULL,
    disease           text        NOT NULL DEFAULT '',
    district          text        NOT NULL,
    year              int         NOT NULL,
    week              int         NOT NULL,
    -- Forecast distribution of the target quantity:
    pred_cases_mean   numeric,
    pred_cases_sd     numeric,
    pred_cases_lwr95  numeric,
    pred_cases_median numeric,
    pred_cases_upr95  numeric,
    -- Endemic-channel baseline + alarm outputs:
    endemic_mean      numeric,
    endemic_sd        numeric,
    outbreak_z        numeric,
    alarm_threshold   numeric,
    alarm_prob        numeric     CHECK (alarm_prob IS NULL OR (alarm_prob >= 0 AND alarm_prob <= 1)),
    alarm_flag        int,        -- 1 <=> alarm_prob >= 0.5 (writer enforces)
    n_draws           int,        -- sampling draws (ewars_lite)
    attrs             jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- model-specific extras
    predicted_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, model_name, disease, district, year, week)
);
CREATE INDEX IF NOT EXISTS idx_ml_pred_model_period ON ops_ml.ml_predictions (model_name, year, week);
CREATE INDEX IF NOT EXISTS idx_ml_pred_district     ON ops_ml.ml_predictions (district);
CREATE INDEX IF NOT EXISTS idx_ml_pred_model_recent ON ops_ml.ml_predictions (model_name, predicted_at DESC);

-- Deployment registry (one row per model; the DAG reads this)
CREATE TABLE IF NOT EXISTS ops_ml.model_deployment (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name      text        NOT NULL UNIQUE,      -- registry key
    model_alias     text        NOT NULL DEFAULT 'champion',
    deployment_kind text        NOT NULL CHECK (deployment_kind IN ('batch', 'on_demand')),
    schedule        text,       -- cron expr for batch (informational; the DAG itself is weekly)
    enabled         boolean     NOT NULL DEFAULT true,
    params          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Monitoring
CREATE TABLE IF NOT EXISTS ops_ml.model_drift (
    run_id      uuid        NOT NULL REFERENCES ops_ml.ml_run (id) ON DELETE CASCADE,
    model_name  text        NOT NULL,
    feature     text        NOT NULL,
    psi         numeric,
    ks          numeric,
    drifted     boolean,
    computed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_model_drift_model ON ops_ml.model_drift (model_name, computed_at DESC);

CREATE TABLE IF NOT EXISTS ops_ml.model_performance (
    model_name    text        NOT NULL,
    model_version text        NOT NULL,
    window_start  date,
    window_end    date,
    metric        text        NOT NULL,
    value         numeric,
    computed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_model_perf_model ON ops_ml.model_performance (model_name, metric, computed_at DESC);

COMMENT ON TABLE ops_ml.ml_run IS
    'One row per runner invocation (train / batch / ad-hoc). Status tracks lifecycle; mlflow_run_id + reference_profile_uri link back to MLflow.';
COMMENT ON TABLE ops_ml.ml_predictions IS
    'Unified flat forecast store. One row per (run by model by disease by district by ISO year/week). Writers DELETE by run_id then INSERT (re-run safe). Dashboards read the latest successful run.';
COMMENT ON TABLE ops_ml.model_deployment IS
    'Scheduling registry: one row per model. The weekly Airflow DAG iterates enabled batch rows; scheduling a model = INSERT/UPDATE here.';
COMMENT ON TABLE ops_ml.model_drift IS
    'Per-run feature drift (PSI/KS) vs the training reference profile.';
COMMENT ON TABLE ops_ml.model_performance IS
    'Windowed accuracy metrics vs mart actuals.';

-- Grants: ops_writer/ops_gateway/ops_reader from earlier init
-- DELETE is included for the writers: the ml_predictions/model_drift write
-- contract is DELETE-by-run_id-then-INSERT (idempotent re-runs).
GRANT USAGE ON SCHEMA ops_ml TO ops_writer, ops_gateway, ops_reader;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ops_ml.ml_run, ops_ml.ml_predictions, ops_ml.model_deployment,
       ops_ml.model_drift, ops_ml.model_performance
    TO ops_writer, ops_gateway;
GRANT SELECT
    ON ops_ml.ml_run, ops_ml.ml_predictions, ops_ml.model_deployment,
       ops_ml.model_drift, ops_ml.model_performance
    TO ops_reader;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops_ml TO ops_writer, ops_gateway;

ALTER DEFAULT PRIVILEGES IN SCHEMA ops_ml
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ops_writer, ops_gateway;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops_ml
    GRANT SELECT ON TABLES TO ops_reader;

SQL

echo "[11_ml_predictions] ops_ml schema provisioned · ml_run + ml_predictions + model_deployment + model_drift + model_performance ready"
