#!/bin/bash
# Project review: the unit of graduation review is the project. An owner
# submits a project to become public; review is an automated essential-files
# check plus a human reviewer who opens the project, runs its notebooks and
# approves. On approval the project becomes public and a gateway hook
# integrates its model.
#
# Adds the review state to ops_workspace.project (22_project.sh) and a
# per-verdict audit table ops_workspace.project_review. For the graduation
# path this replaces the artifact-centric ops_artifact.validator_result /
# promotion_log trail, which remains for artifact rows.
#
# A separate idempotent numbered migration (ADD COLUMN / CREATE TABLE ... IF NOT
# EXISTS plus explicit grants), like 13/22/26/28: it runs at initdb and also
# brings an existing cluster up to date. bundle/scripts/seed-app-security.sh
# re-applies it in its ops-migration loop after restore-state, so restoring an
# older dump keeps the columns.
#
# Idempotent.
set -euo pipefail
OPS_DB="${OPS_DB_NAME:-nhdw_ops}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$OPS_DB" <<'SQL'

-- project review state (extends ops_workspace.project)
-- review_status lifecycle:
--   draft              never submitted (the default for every existing row)
--   in_review          owner submitted; awaiting a human reviewer
--   changes_requested  reviewer bounced it back for fixes (re-submittable)
--   approved           reviewer approved; project made public + model hook fired
--   rejected           reviewer rejected (terminal for this submission)
-- ADD COLUMN IF NOT EXISTS is idempotent: an existing column (incl. its inline
-- CHECK) is left untouched, so a re-run never errors or duplicates the check.
ALTER TABLE ops_workspace.project
    ADD COLUMN IF NOT EXISTS review_status text NOT NULL DEFAULT 'draft'
        CHECK (review_status IN
               ('draft','in_review','changes_requested','approved','rejected'));
ALTER TABLE ops_workspace.project
    ADD COLUMN IF NOT EXISTS submitted_at    timestamptz;
ALTER TABLE ops_workspace.project
    ADD COLUMN IF NOT EXISTS submitted_by    uuid;
ALTER TABLE ops_workspace.project
    ADD COLUMN IF NOT EXISTS reviewed_at     timestamptz;
ALTER TABLE ops_workspace.project
    ADD COLUMN IF NOT EXISTS reviewed_by     uuid;
-- The last essential-files checklist computed at submit time (an array of
-- {requirement, present, matched:[...]}). Lets the reviewer + UI see what the
-- automated check found without recomputing.
ALTER TABLE ops_workspace.project
    ADD COLUMN IF NOT EXISTS review_checklist jsonb;

COMMENT ON COLUMN ops_workspace.project.review_status IS
    'PROJECT-centric graduation state: draft|in_review|changes_requested|'
    'approved|rejected. Owner submits (status in_review); a human reviewer approves '
    '(status approved, visibility public + model integration hook) or bounces it.';

-- Reviewers hit "the queue" (review_status IN in_review/changes_requested)
-- constantly, so index it.
CREATE INDEX IF NOT EXISTS idx_project_review_status
    ON ops_workspace.project (review_status)
    WHERE review_status IN ('in_review','changes_requested');

-- per-verdict review audit
-- One durable row per reviewer verdict (append-only trail). Mirrors the intent
-- of the old ops_artifact.artifact_review but keyed on the PROJECT.
CREATE TABLE IF NOT EXISTS ops_workspace.project_review (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid        NOT NULL REFERENCES ops_workspace.project (id) ON DELETE CASCADE,
    reviewer     uuid        NOT NULL REFERENCES ops_identity."user" (kc_sub),
    verdict      text        NOT NULL
                 CHECK (verdict IN ('approved','changes_requested','rejected','pending')),
    comment      text,
    checklist    jsonb,
    reviewed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_project_review_project
    ON ops_workspace.project_review (project_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_review_reviewer
    ON ops_workspace.project_review (reviewer);

COMMENT ON TABLE ops_workspace.project_review IS
    'Append-only audit of PROJECT graduation verdicts. One row per reviewer '
    'decision (approved|changes_requested|rejected|pending) with the checklist '
    'snapshot + comment. Supersedes ops_artifact.artifact_review for the '
    'project-centric review flow (2026-07-08).';

-- Grants: these tables/columns are created by the superuser (not ops_writer),
-- so the ALTER DEFAULT PRIVILEGES in 06 don't cover them; grant explicitly,
-- exactly like 22_project.sh / 13_graduation.sh. The gateway needs
-- SELECT/INSERT/UPDATE on project_review (write a verdict, read the trail); the
-- new project.* columns inherit the existing table grants (already SELECT/
-- INSERT/UPDATE to ops_writer/ops_gateway from 22).
GRANT SELECT, INSERT, UPDATE ON ops_workspace.project_review TO ops_writer, ops_gateway;
GRANT SELECT ON ops_workspace.project_review TO ops_reader;
SQL

echo "[29_project_review] project.review_status + ops_workspace.project_review ready"
