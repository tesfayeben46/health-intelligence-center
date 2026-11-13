#!/bin/bash
# Project scoping for the workshop artifact catalogue. Links
# ops_artifact.artifact rows to a project without breaking existing paths: the
# new columns are nullable, so project-less submits and the widget-plugin
# upload flow keep working.
#
# Two columns:
#   project_id         cross-schema link ops_artifact.artifact ->
#                      ops_workspace.project (22_project.sh). NULL for
#                      plugins and rows without a project.
#   mlflow_model_name  explicit link from the artifact to its MLflow registered
#                      model, used instead of matching the artifact title
#                      against model names. NULL until a model artifact is bound.
#
# A separate idempotent numbered migration instead of an edit to
# 05_ops_schemas.sql: 05 runs only at initdb time, while this file also brings
# an existing cluster up to date (same pattern as 22_project.sh).
#
# Restore ordering: bundle/scripts/seed-app-security.sh re-runs this file after
# restore-state's `pg_restore --clean` on nhdw_ops. A dump older than these
# columns recreates ops_artifact.artifact without them; the re-run adds them
# back.
#
# Idempotent: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS and a
# guarded GRANT DO block (as in 17_perm_schemas.sql). Writes no data.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- columns
-- Cross-schema FK ops_artifact -> ops_workspace. NULLABLE so plugins/legacy
-- rows and today's project-less submits are unaffected (additive invariant).
ALTER TABLE ops_artifact.artifact
    ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES ops_workspace.project (id);

-- Explicit artifact -> MLflow registered-model bridge (replaces the title match).
ALTER TABLE ops_artifact.artifact
    ADD COLUMN IF NOT EXISTS mlflow_model_name text;

COMMENT ON COLUMN ops_artifact.artifact.project_id IS
    'B2/B4 project this artifact belongs to (ops_workspace.project.id). '
    'NULLABLE: plugins/legacy/unscoped rows carry no project. The project '
    'invariant is enforced additively; never replaces the tier/workshop_group '
    'visibility ACL, it AND-composes with it.';
COMMENT ON COLUMN ops_artifact.artifact.mlflow_model_name IS
    'For kind=model: the MLflow registered-model name that backs this artifact '
    '(execution key into MLflow/ops_ml). Explicit bridge replacing the fragile '
    'title==registered-model-name fuzzy match. NULL until bound.';

-- indexes
CREATE INDEX IF NOT EXISTS idx_artifact_project      ON ops_artifact.artifact (project_id);
CREATE INDEX IF NOT EXISTS idx_artifact_kind_project ON ops_artifact.artifact (kind, project_id);

-- grants
-- The new columns ride the existing table grants, but re-assert them behind a
-- role guard (like 17_perm_schemas.sql) so this file is self-contained and safe
-- to re-exec against a live pod whatever the current grant state. The gateway
-- (ops_gateway) and ops_writer read+write artifact rows; ops_reader reads.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_artifact.artifact TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_artifact.artifact TO ops_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_artifact.artifact TO ops_reader;
    END IF;
END $$;
SQL

echo "[26_artifact_project] ops_artifact.artifact.project_id + mlflow_model_name ready"
