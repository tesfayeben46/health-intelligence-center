#!/bin/bash
# Adds columns to the ops_ml drift and performance tables for the generic
# monitoring DAG (5_orchestrator/dags/ml_drift_check.py) and the ml-runner
# `drift-check` / `score-performance` verbs (2_pipelines/ml/runner).
#
# The base tables (model_drift, model_performance) are created in
# 11_ml_predictions.sh. This migration only adds columns the monitoring writers
# populate and never redefines the tables, so it is safe on an initialised DB.
#
#   model_drift        +kind          numeric | categorical (drift metric family)
#                      +psi_threshold the threshold this row was judged against
#                      +note          e.g. 'first-seen' for a bootstrapped baseline
#   model_performance  +run_id        the predictions run that was scored (link)
#                      +sample_size   number of (prediction, actual) pairs behind the metric
#
# Idempotent: every statement is ADD COLUMN IF NOT EXISTS. Re-running is a
# no-op. Grants are table-level (11_*), so new columns inherit them.
set -euo pipefail

OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- The base tables live in 11_ml_predictions.sh; guard in case ordering ever
-- changes so this migration is self-contained (matches those definitions).
CREATE TABLE IF NOT EXISTS ops_ml.model_drift (
    run_id      uuid        NOT NULL REFERENCES ops_ml.ml_run (id) ON DELETE CASCADE,
    model_name  text        NOT NULL,
    feature     text        NOT NULL,
    psi         numeric,
    ks          numeric,
    drifted     boolean,
    computed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_ml.model_performance (
    model_name    text        NOT NULL,
    model_version text        NOT NULL,
    window_start  date,
    window_end    date,
    metric        text        NOT NULL,
    value         numeric,
    computed_at   timestamptz NOT NULL DEFAULT now()
);

-- model_drift enrichment
ALTER TABLE ops_ml.model_drift ADD COLUMN IF NOT EXISTS kind          text;
ALTER TABLE ops_ml.model_drift ADD COLUMN IF NOT EXISTS psi_threshold numeric;
ALTER TABLE ops_ml.model_drift ADD COLUMN IF NOT EXISTS note          text;
COMMENT ON COLUMN ops_ml.model_drift.kind IS
    'Drift metric family for the feature: numeric | categorical.';
COMMENT ON COLUMN ops_ml.model_drift.note IS
    'Optional marker, e.g. ''first-seen'' when the reference profile was bootstrapped from the first batch.';

-- model_performance enrichment
ALTER TABLE ops_ml.model_performance ADD COLUMN IF NOT EXISTS run_id      uuid;
ALTER TABLE ops_ml.model_performance ADD COLUMN IF NOT EXISTS sample_size bigint;
COMMENT ON COLUMN ops_ml.model_performance.run_id IS
    'The ops_ml.ml_run predictions run that was scored (link, not a FK; the run may be pruned).';
COMMENT ON COLUMN ops_ml.model_performance.sample_size IS
    'Number of (prediction <-> actual) pairs behind the metric value.';

CREATE INDEX IF NOT EXISTS idx_model_drift_run ON ops_ml.model_drift (run_id);

SQL

echo "[27_ml_monitoring] model_drift(+kind,psi_threshold,note) + model_performance(+run_id,sample_size) ready"
