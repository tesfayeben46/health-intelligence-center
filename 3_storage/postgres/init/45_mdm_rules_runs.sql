-- The matcher's rules registry and run log.
--
-- The candidate upsert replaces matcher_run_uid / discovered_at on each
-- pending candidate, so the candidate table alone cannot say when a
-- suggestion was first seen or which settings produced it. This file adds
-- three records:
--
--   mdm_match_rule          one row per version of a rule's settings
--                           (enabled, threshold). Append-only: a change
--                           stamps recorded_to on the current row and
--                           inserts the next version. The matcher reads the
--                           current rows; the Setup screen shows them.
--   mdm_matcher_run         one row per matcher run: when, which version,
--                           the settings it actually used, what it found.
--   mdm_candidate_sighting  which run saw which candidate (new or refreshed),
--                           so first-seen / last-seen survive the upsert.
--
-- Nothing here is read by the vault; mastering still changes only through
-- mdm_match_decision. With these tables empty or absent the matcher falls
-- back to its env threshold and records no trace.

\connect nhdw_ops

CREATE TABLE IF NOT EXISTS ops_governance.mdm_match_rule (
    rule_version_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deployment_context  text        NOT NULL DEFAULT 'ET',
    entity_class_code   text        NOT NULL,          -- 'facility' | 'commodity'
    rule_code           text        NOT NULL,          -- the matcher's match_rule value
    label               text        NOT NULL,
    description         text        NOT NULL,
    threshold           numeric     NOT NULL CHECK (threshold > 0 AND threshold <= 1),
    enabled             boolean     NOT NULL DEFAULT true,
    version             integer     NOT NULL CHECK (version >= 1),
    changed_by          text        NOT NULL DEFAULT 'system',
    change_note         text,
    recorded_from       timestamptz NOT NULL DEFAULT now(),
    recorded_to         timestamptz,
    UNIQUE (deployment_context, entity_class_code, rule_code, version)
);
COMMENT ON TABLE ops_governance.mdm_match_rule IS
    'Matcher rule settings, one row per version (append-only; current = recorded_to IS NULL). '
    'threshold = the minimum score a pair needs for this rule to propose it.';
CREATE UNIQUE INDEX IF NOT EXISTS uq_mdm_match_rule_current
    ON ops_governance.mdm_match_rule (deployment_context, entity_class_code, rule_code)
    WHERE recorded_to IS NULL;

-- Version 1 of every rule matches matcher.py's built-in behaviour (exact
-- rules score 1.0; similarity at the 0.85 the job manifest sets), so seeding
-- them changes no behaviour.
INSERT INTO ops_governance.mdm_match_rule
       (deployment_context, entity_class_code, rule_code, label, description, threshold, version, change_note)
SELECT v.ctx, v.cls, v.code, v.label, v.descr, v.threshold, 1, 'initial settings, as built into matcher 0.1.0'
FROM (VALUES
  ('ET', 'facility',  'name_exact',         'Exact name',
   'Name matches one master', 1.00),
  ('ET', 'facility',  'name_ambiguous',     'Ambiguous name',
   'Name matches several masters', 1.00),
  ('ET', 'facility',  'name_probabilistic', 'Probabilistic',
   'Similar name', 0.85),
  ('ET', 'commodity', 'name_probabilistic', 'Probabilistic',
   'Similar product name', 0.85)
) AS v(ctx, cls, code, label, descr, threshold)
WHERE NOT EXISTS (
    SELECT 1 FROM ops_governance.mdm_match_rule r
    WHERE r.deployment_context = v.ctx AND r.entity_class_code = v.cls AND r.rule_code = v.code);

CREATE TABLE IF NOT EXISTS ops_governance.mdm_matcher_run (
    run_uid             uuid        PRIMARY KEY,
    deployment_context  text        NOT NULL,
    matcher_version     text        NOT NULL,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    status              text        NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running','succeeded','failed')),
    settings            jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- rules as used, and where they came from
    candidates_total    integer,
    candidates_new      integer,
    candidates_refreshed integer,
    counts              jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- per entity class, per rule
    error               text
);
COMMENT ON TABLE ops_governance.mdm_matcher_run IS
    'One row per matcher run: settings used and what it proposed. A run left in '
    '''running'' with no finished_at died without reporting.';
CREATE INDEX IF NOT EXISTS ix_mdm_matcher_run_recent
    ON ops_governance.mdm_matcher_run (deployment_context, started_at DESC);

CREATE TABLE IF NOT EXISTS ops_governance.mdm_candidate_sighting (
    run_uid       uuid    NOT NULL REFERENCES ops_governance.mdm_matcher_run (run_uid),
    candidate_id  uuid    NOT NULL REFERENCES ops_governance.mdm_match_candidate (candidate_id)
                          ON DELETE CASCADE,
    score         numeric NOT NULL,
    match_rule    text    NOT NULL,
    outcome       text    NOT NULL CHECK (outcome IN ('new','refreshed')),
    PRIMARY KEY (run_uid, candidate_id)
);
COMMENT ON TABLE ops_governance.mdm_candidate_sighting IS
    'Which matcher run proposed or refreshed which pending candidate. Decided candidates '
    'are never refreshed, so they gain no sightings after the decision.';
CREATE INDEX IF NOT EXISTS ix_mdm_candidate_sighting_candidate
    ON ops_governance.mdm_candidate_sighting (candidate_id);

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        -- the matcher: reads the rules, writes its own run trail
        GRANT SELECT ON ops_governance.mdm_match_rule TO ops_writer;
        GRANT SELECT, INSERT, UPDATE ON ops_governance.mdm_matcher_run TO ops_writer;
        GRANT SELECT, INSERT ON ops_governance.mdm_candidate_sighting TO ops_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        -- the console reads all three (Setup, record history); it writes none
        GRANT SELECT ON ops_governance.mdm_match_rule,
                        ops_governance.mdm_matcher_run,
                        ops_governance.mdm_candidate_sighting TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_governance.mdm_match_rule,
                        ops_governance.mdm_matcher_run,
                        ops_governance.mdm_candidate_sighting TO ops_reader;
    END IF;
END $$;
