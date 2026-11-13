#!/bin/bash
# Datasets as an independent, many-to-many entity.
#
#   - A project is the umbrella: models, notebooks, pipelines and reports
#     belong to exactly one project (ops_artifact.artifact.project_id,
#     enforced in the gateway for those kinds).
#   - Datasets do not require a project and can be linked to many projects.
#     The single artifact.project_id FK cannot express that, so this
#     migration adds the M:N link table and moves datasets onto it.
#
# Idempotent numbered migration (same pattern as 26_artifact_project.sh): runs
# at initdb and is re-run by bundle/scripts/seed-app-security.sh against a live
# pod or after a pg_restore. CREATE IF NOT EXISTS, ON CONFLICT and a guarded
# backfill make it safe to re-run.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- the many-to-many link
-- A dataset (ops_artifact.artifact kind='dataset') connected to a project. A
-- dataset may have zero links (independent/unattached) or many (shared into
-- several projects); a project may reference many datasets. The dataset's own
-- access level stays on artifact.tier and linking never changes it (a private
-- dataset stays private even if linked into a public project; the reader still
-- needs to satisfy the dataset's tier ACL).
CREATE TABLE IF NOT EXISTS ops_artifact.project_dataset (
    project_id  uuid        NOT NULL REFERENCES ops_workspace.project (id) ON DELETE CASCADE,
    dataset_id  uuid        NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    added_by    uuid        REFERENCES ops_identity.user (kc_sub),
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id)
);
CREATE INDEX IF NOT EXISTS idx_project_dataset_dataset ON ops_artifact.project_dataset (dataset_id);

COMMENT ON TABLE ops_artifact.project_dataset IS
    'M:N link: datasets (independent entity) connected to projects. A dataset '
    'keeps its own tier ACL; linking does not change visibility. Models/notebooks/'
    'pipelines/reports still use artifact.project_id (belong to ONE project).';

-- grants (guarded, self-contained; mirrors 26_artifact_project.sh)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT, INSERT, DELETE ON ops_artifact.project_dataset TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_artifact.project_dataset TO ops_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_artifact.project_dataset TO ops_reader;
    END IF;
END $$;

-- backfill: existing datasets bound via the old single project_id FK become
-- link rows, then their project_id is cleared so the link table is the SOLE
-- source of dataset<->project relationships (datasets are independent now).
-- Guarded + idempotent: the INSERT skips existing links; the UPDATE only fires
-- on dataset rows that still carry a (now-legacy) project_id.
INSERT INTO ops_artifact.project_dataset (project_id, dataset_id, added_by)
SELECT a.project_id, a.id, a.owner
FROM ops_artifact.artifact a
WHERE a.kind = 'dataset' AND a.project_id IS NOT NULL
ON CONFLICT (project_id, dataset_id) DO NOTHING;

UPDATE ops_artifact.artifact
   SET project_id = NULL
 WHERE kind = 'dataset' AND project_id IS NOT NULL;
SQL

echo "[28_dataset_project_link] ops_artifact.project_dataset ready (datasets independent, M:N)"
