#!/bin/bash
# Adds the validator_result and promotion_log tables, giving the graduation
# pathway an audit trail per artifact.
#
# The ops_artifact tables from 05_ops_schemas.sql (peer_review_status,
# artifact_version) cover ownership and version history; these tables log
# validator and promotion decisions.
#
# Idempotent.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

CREATE TABLE IF NOT EXISTS ops_artifact.validator_result (
    id              bigserial   PRIMARY KEY,
    artifact_id     uuid        NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    artifact_version integer    NOT NULL,
    validator       text        NOT NULL,            -- 'schema'|'latency'|'deps'|'resources'|'smoke'
    ran_at          timestamptz NOT NULL DEFAULT now(),
    duration_ms     integer,
    passed          boolean     NOT NULL,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error_msg       text
);
CREATE INDEX IF NOT EXISTS idx_validator_artifact
    ON ops_artifact.validator_result (artifact_id, artifact_version);
CREATE INDEX IF NOT EXISTS idx_validator_passed
    ON ops_artifact.validator_result (passed, ran_at DESC);

CREATE TABLE IF NOT EXISTS ops_artifact.promotion_log (
    id              bigserial   PRIMARY KEY,
    artifact_id     uuid        NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    from_stage      text        NOT NULL,           -- 'unreviewed' | 'in_review' | 'approved'
    to_stage        text        NOT NULL,           -- same enum
    promoted_by     uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    promoted_at     timestamptz NOT NULL DEFAULT now(),
    mlflow_model_name     text,                      -- the canonical model name in MLflow once promoted
    mlflow_model_version  text,
    note            text
);
CREATE INDEX IF NOT EXISTS idx_promotion_artifact
    ON ops_artifact.promotion_log (artifact_id, promoted_at DESC);

COMMENT ON TABLE ops_artifact.validator_result IS
    'Per-artifact-version validator outcomes. Used as the precondition for promotion: every required validator must show passed=true.';
COMMENT ON TABLE ops_artifact.promotion_log IS
    'Append-only audit of every stage transition in the graduation pathway. Joins ops_artifact.artifact + MLflow model registry entries.';

GRANT SELECT, INSERT ON ops_artifact.validator_result, ops_artifact.promotion_log
    TO ops_writer, ops_gateway;
GRANT SELECT ON ops_artifact.validator_result, ops_artifact.promotion_log
    TO ops_reader;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops_artifact TO ops_writer, ops_gateway;
SQL

echo "[13_graduation] validator_result + promotion_log ready"
