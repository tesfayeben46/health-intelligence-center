-- MDM decision tier: match candidates and the append-only steward decision log.
--
-- The MDM engine (2_pipelines/mdm/) writes candidate pairs here (deterministic
-- and probabilistic matches with scores); a steward accepts, rejects or merges
-- in the admin console (gateway routes/mdm.py). Every verdict lands in
-- mdm_match_decision, which is append-only and bitemporal because it is the
-- reproducibility contract: the vault build reads it, so a rebuild from sources
-- plus decisions yields identical golden masters. Supersede a decision by
-- stamping recorded_to and inserting a new row; never UPDATE or DELETE a
-- decision in place.
--
-- deployment_context keeps country deployments apart (e.g. ET, ZM);
-- cross-context matching is never allowed.

\connect nhdw_ops

-- Candidate pairs proposed by the matcher. Regenerated each matcher run
-- (upsert on the pair identity); steward-facing status only moves forward
-- (pending -> decided) via the gateway, never by the matcher.
CREATE TABLE IF NOT EXISTS ops_governance.mdm_match_candidate (
    candidate_id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_context       text        NOT NULL DEFAULT 'ET',
    entity_class_code        text        NOT NULL,          -- 'facility' | 'commodity'
    left_source_system_code  text        NOT NULL,          -- e.g. 'HCMIS'
    left_source_record_key   text        NOT NULL,          -- the source hub business key
    right_source_system_code text        NOT NULL,          -- e.g. 'mBrana'
    right_source_record_key  text        NOT NULL,
    suggested_master_bk      text,                          -- the conformed_bk the pair would share
    score                    numeric     NOT NULL,          -- 1.0 = deterministic
    match_rule               text        NOT NULL,          -- 'slug_exact' | 'name_exact' | 'name_probabilistic' | 'geo_tiebreak'
    match_features           jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- similarity, distance_km, blocking key…
    status                   text        NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','auto_accepted','decided','superseded')),
    matcher_run_uid          uuid,
    matcher_version          text,
    discovered_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (deployment_context, entity_class_code,
            left_source_system_code, left_source_record_key,
            right_source_system_code, right_source_record_key)
);
COMMENT ON TABLE ops_governance.mdm_match_candidate IS
    'Matcher-proposed same-entity pairs with scores. Only exact-key/unambiguous-exact-name '
    'rules may auto-accept; everything probabilistic waits for a steward (a wrong merge is '
    'worse than a missed one).';
CREATE INDEX IF NOT EXISTS ix_mdm_candidate_queue
    ON ops_governance.mdm_match_candidate (deployment_context, entity_class_code, status, score DESC);

-- The append-only steward decision log (bitemporal).
--   valid_from/valid_to       when the mastering statement is true in the world
--   recorded_from/recorded_to when the platform knew it (supersession stamps recorded_to)
CREATE TABLE IF NOT EXISTS ops_governance.mdm_match_decision (
    decision_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_context  text        NOT NULL DEFAULT 'ET',
    entity_class_code   text        NOT NULL,
    source_system_code  text        NOT NULL,
    source_record_key   text        NOT NULL,
    decision            text        NOT NULL CHECK (decision IN ('accept','reject','merge')),
    master_bk           text,                                -- the conformed_bk this source row maps to (accept/merge)
    candidate_id        uuid        REFERENCES ops_governance.mdm_match_candidate(candidate_id),
    score               numeric,
    decided_by          text        NOT NULL,                -- kc username (audited via ops_audit.data_event too)
    note                text,
    valid_from          date        NOT NULL DEFAULT current_date,
    valid_to            date        NOT NULL DEFAULT '2099-12-31',
    recorded_from       timestamptz NOT NULL DEFAULT now(),
    recorded_to         timestamptz                          -- NULL = the current record of knowledge
);
COMMENT ON TABLE ops_governance.mdm_match_decision IS
    'Append-only, bitemporal steward verdicts: the reproducibility contract the vault '
    'build reads. Supersede by stamping recorded_to + inserting anew; never UPDATE/DELETE.';
-- one current decision per source record per context/class
CREATE UNIQUE INDEX IF NOT EXISTS uq_mdm_decision_current
    ON ops_governance.mdm_match_decision
       (deployment_context, entity_class_code, source_system_code, source_record_key)
    WHERE recorded_to IS NULL;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        -- console reads the queue + writes decisions; supersession = UPDATE of recorded_to only
        GRANT SELECT, UPDATE ON ops_governance.mdm_match_candidate TO ops_gateway;
        GRANT SELECT, INSERT, UPDATE ON ops_governance.mdm_match_decision TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        -- the matcher job upserts candidates
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_governance.mdm_match_candidate TO ops_writer;
        GRANT SELECT ON ops_governance.mdm_match_decision TO ops_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_governance.mdm_match_candidate, ops_governance.mdm_match_decision TO ops_reader;
    END IF;
END $$;
