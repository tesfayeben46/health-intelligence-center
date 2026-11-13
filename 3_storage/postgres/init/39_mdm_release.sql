-- Master-data releases: a named, reproducible cut of "who the masters were".
--
-- The warehouse resolves masters live: the resolution views read the steward
-- decision log as it stands, so today's grouping reflects today's decisions.
-- That is right for operations and wrong for a published number: a report
-- re-run next month can legitimately return a different facility count, and
-- nothing in the report says which master data produced it.
--
-- A release pins both inputs that determine a master, because either one
-- moving changes the answer:
--
--   decisions_as_of   a timestamp cut of ops_governance.mdm_match_decision.
--                     The log is already bitemporal, so "the
--                     decisions the stewards had made at T" is
--                     recorded_from <= T AND (recorded_to IS NULL OR recorded_to > T).
--
--   source_watermark  the vault load_ts the masters were derived from. A
--                     decision cut alone does not freeze the answer: a
--                     facility that arrived from DHIS2 after T still joins its
--                     cluster, because clustering is derived from source
--                     records, not only from decisions.
--
-- Together they are sufficient: same decisions + same source records => same
-- masters, by construction of the resolution views.
--
-- A release does not freeze a copy of the data. It records the two
-- coordinates and the vault reproduces the masters from them. That keeps
-- releases cheap (a row, not a snapshot) and transparent (a restated source
-- correction is visible instead of hidden inside a frozen copy).

\connect nhdw_ops

CREATE TABLE IF NOT EXISTS ops_governance.mdm_release (
    release_id        text        PRIMARY KEY,          -- e.g. 'R-2026-09': human-readable, quotable in a report
    deployment_context text       NOT NULL DEFAULT 'ET',
    entity_class_code text        NOT NULL DEFAULT 'facility',
    decisions_as_of   timestamptz NOT NULL,             -- the decision-log cut
    source_watermark  timestamptz NOT NULL,             -- max vault load_ts at cut time
    note              text,
    -- a release is immutable once published: re-running a pinned report must
    -- never silently resolve differently. Correcting one means publishing a
    -- successor and saying so.
    status            text        NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'published', 'superseded')),
    superseded_by     text        REFERENCES ops_governance.mdm_release(release_id),
    created_by        text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    published_at      timestamptz
);
COMMENT ON TABLE ops_governance.mdm_release IS
    'Named master-data cuts. A release pins the decision-log timestamp AND the '
    'source watermark; either alone is insufficient to reproduce a master set. '
    'Published releases are immutable; correct by superseding.';

-- Counts captured at publication, so a release can be audited without
-- re-deriving it (and so a drift between the pinned coordinates and what the
-- vault reproduces is detectable, not silent).
CREATE TABLE IF NOT EXISTS ops_governance.mdm_release_stat (
    release_id      text   NOT NULL REFERENCES ops_governance.mdm_release(release_id) ON DELETE CASCADE,
    stat_key        text   NOT NULL,        -- 'masters' | 'conformed' | 'single_source' | 'decisions_applied'
    stat_value      bigint NOT NULL,
    PRIMARY KEY (release_id, stat_key)
);
COMMENT ON TABLE ops_governance.mdm_release_stat IS
    'Master counts as they were when the release was published: the audit '
    'anchor a later re-derivation is compared against.';

CREATE INDEX IF NOT EXISTS ix_mdm_release_published
    ON ops_governance.mdm_release (deployment_context, entity_class_code, published_at DESC)
    WHERE status = 'published';

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT, INSERT, UPDATE ON ops_governance.mdm_release TO ops_gateway;
        GRANT SELECT, INSERT, DELETE ON ops_governance.mdm_release_stat TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_governance.mdm_release, ops_governance.mdm_release_stat TO ops_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT ON ops_governance.mdm_release, ops_governance.mdm_release_stat TO ops_writer;
    END IF;
END $$;
