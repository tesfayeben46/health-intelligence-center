-- DQ observability store.
--
-- Adds to the nhdw_ops database:
--   1. ops_governance.dq_threshold: the single home for the SLA and band values
--      used by the gateway (routes/dq.py SOURCES.sla_hours) and the admin DQ
--      widget (pctTone 90/75, agreement 85/60). Consumers read these rows and
--      fall back to built-in defaults when a row is absent, so this table
--      cannot take the DQ surface down.
--   2. dq_flag.fingerprint: detector idempotency, one open flag per distinct
--      finding (partial-unique on non-resolved rows, so a resolved finding may
--      legitimately re-open as a new row later).
--
-- Additive; safe to re-run (IF NOT EXISTS throughout). An existing cluster
-- needs a one-time apply (init runs only on a fresh PGDATA).
\connect nhdw_ops

CREATE TABLE IF NOT EXISTS ops_governance.dq_threshold (
    scope       text        NOT NULL
                  CONSTRAINT dq_threshold_scope_chk CHECK (scope IN ('sla_hours', 'ui_band')),
    key         text        NOT NULL,   -- source key 'DHIS2' (sla_hours) | band name 'scorecard_good' (ui_band)
    value       numeric     NOT NULL,
    unit        text        NOT NULL
                  CONSTRAINT dq_threshold_unit_chk CHECK (unit IN ('hours', 'pct')),
    note        text,
    managed_by  text        NOT NULL DEFAULT 'seed',   -- 'seed' | 'runtime' (reconciliation provenance, as alert_routing)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- Composite natural PK: a deliberate exception to the id-PK convention for
    -- a few config rows addressed only by (scope, key), never referenced externally.
    PRIMARY KEY (scope, key)
);

COMMENT ON TABLE ops_governance.dq_threshold IS
    'Central DQ SLA/band config. Absent row => consumer''s coded default (fail-open to previous behaviour).';

-- Seed values equal the consumers' built-in defaults, so seeding changes no behaviour.
INSERT INTO ops_governance.dq_threshold (scope, key, value, unit, note) VALUES
    ('sla_hours', 'DHIS2',      6,    'hours', 'ingestion freshness SLA (was routes/dq.py)'),
    ('sla_hours', 'EthioEMR',   1,    'hours', 'ingestion freshness SLA (was routes/dq.py)'),
    ('sla_hours', 'HCMIS',      4,    'hours', 'ingestion freshness SLA (was routes/dq.py)'),
    ('sla_hours', 'mBrana',     2,    'hours', 'ingestion freshness SLA (was routes/dq.py)'),
    ('sla_hours', 'eLMIS-hist', 720,  'hours', 'immutable archive lane: re-run on demand'),
    ('sla_hours', 'MFR',        48,   'hours', 'ingestion freshness SLA (was routes/dq.py)'),
    ('sla_hours', 'Climate',    168,  'hours', 'ingestion freshness SLA (was routes/dq.py)'),
    ('ui_band',   'scorecard_good',  90, 'pct', 'admin DQ scorecard green threshold (was pctTone default)'),
    ('ui_band',   'scorecard_watch', 75, 'pct', 'admin DQ scorecard amber threshold (was pctTone default)'),
    ('ui_band',   'agreement_good',  85, 'pct', 'cross-source agreement green (was adm-dq literal)'),
    ('ui_band',   'agreement_watch', 60, 'pct', 'cross-source agreement amber (was adm-dq literal)')
ON CONFLICT (scope, key) DO UPDATE
    SET value = EXCLUDED.value, unit = EXCLUDED.unit, note = EXCLUDED.note, updated_at = now()
  WHERE ops_governance.dq_threshold.managed_by = 'seed';

-- Detector idempotency on the existing incident table.
ALTER TABLE ops_governance.dq_flag
    ADD COLUMN IF NOT EXISTS fingerprint text;

COMMENT ON COLUMN ops_governance.dq_flag.fingerprint IS
    $c$Stable detector dedupe hash. Schemes: dbt->("dbt", unique_id); stale->("stale", source); volume->("volume", source, table); mdm->("mdm","backlog"); classification->("classification","gap"). Unique while status <> 'resolved' - a wontfix flag keeps absorbing upserts and never re-surfaces; only resolved may re-open as a new row.$c$;

CREATE UNIQUE INDEX IF NOT EXISTS dq_flag_fingerprint_open_uq
    ON ops_governance.dq_flag (fingerprint)
    WHERE fingerprint IS NOT NULL AND status <> 'resolved';

COMMENT ON SCHEMA ops_governance IS
    $c$Access requests + approvals, alert routing, MDM stewardship, and the DATA-QUALITY GOVERNANCE RECORD: thresholds, incident flags, acks and their durable transition history.$c$;
COMMENT ON COLUMN ops_governance.dq_flag.raised_by IS
    $c$'system' | 'detector' (POST /api/dq/detect) | <kc_sub::text> (operator ack path)$c$;
COMMENT ON COLUMN ops_governance.dq_flag.source IS
    $c$Gateway SOURCES registry key (DHIS2, EthioEMR, HCMIS, mBrana, eLMIS-hist, MFR, Climate) | 'dbt' | 'mdm' | 'governance' | 'cross'$c$;
COMMENT ON COLUMN ops_governance.dq_flag.error_class IS
    $c$'dbt_test' (dbt failures incl. fk_orphan/not_null - finer class in key->>'test_class') | 'stale_ingest' | 'volume_outlier' | 'mdm_backlog' | 'classification_gap' (sensitive-looking mart columns with no classification row - masking is a denylist, so these are served in the clear) | legacy 'null_in_required'/'outlier'/'fk_orphan'/'timeliness'$c$;

-- Governance history: incidents are a durable record, not a queue that
-- forgets. Every status transition is appended to
-- dq_flag_event; resolved/updated stamps live on the flag. This history is the
-- raw material for governance KPIs (failure rates per source/layer/rule,
-- time-to-acknowledge/resolve, recurrence) that steer engineering priorities.
ALTER TABLE ops_governance.dq_flag
    ADD COLUMN IF NOT EXISTS updated_at  timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

CREATE TABLE IF NOT EXISTS ops_governance.dq_flag_event (
    id          bigserial   PRIMARY KEY,       -- internal log row, so bigserial (convention in 05_ops_schemas)
    flag_id     uuid        NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now(),
    actor       text        NOT NULL,          -- 'detector' | kc_sub | 'system'
    from_status text,
    to_status   text        NOT NULL,
    note        text
);
CREATE INDEX IF NOT EXISTS dq_flag_event_flag_idx ON ops_governance.dq_flag_event (flag_id, ts);

COMMENT ON TABLE ops_governance.dq_flag_event IS
    'Append-only status-transition history of dq_flag; never deleted; the source for governance KPIs.';

DROP TRIGGER IF EXISTS trg_dq_flag_touch ON ops_governance.dq_flag;
CREATE TRIGGER trg_dq_flag_touch BEFORE UPDATE ON ops_governance.dq_flag
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();
DROP TRIGGER IF EXISTS trg_dq_threshold_touch ON ops_governance.dq_threshold;
CREATE TRIGGER trg_dq_threshold_touch BEFORE UPDATE ON ops_governance.dq_threshold
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT ON ops_governance.dq_threshold TO ops_gateway;
        GRANT SELECT, INSERT, UPDATE ON ops_governance.dq_flag TO ops_gateway;
        GRANT SELECT, INSERT ON ops_governance.dq_flag_event TO ops_gateway;
        -- INSERT on the table is not enough: dq_flag_event.id is a serial, so
        -- the writer also needs its sequence. Without this the detector can
        -- update an existing flag (no history row is written then) but fails
        -- with "permission denied for sequence dq_flag_event_id_seq" as soon as
        -- it raises a new one.
        GRANT USAGE, SELECT ON SEQUENCE ops_governance.dq_flag_event_id_seq TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_governance.dq_threshold, ops_governance.dq_flag_event TO ops_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_governance.dq_threshold TO ops_writer;
        GRANT SELECT ON ops_governance.dq_flag_event TO ops_writer;
    END IF;
END$$;
