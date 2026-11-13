-- Runs inside `nhdw_ops`.
--
-- Six schemas split by concern, not by source. Each answers one application
-- question the analytical vault cannot:
--
--   ops_identity   Who is logged in? What's their data scope?
--                  JIT-cached from Keycloak userinfo on each login.
--                  Never authoritative: Keycloak is.
--
--   ops_workspace  What did the user save / pin / subscribe to?
--                  Saved queries, dashboards, alert subscriptions, prefs.
--
--   ops_governance Who asked for access? What data quality issues are open?
--                  Access requests + decision audit. Data-quality flag inbox.
--
--   ops_export     Every dataset a dashboard generated.
--                  Registry of object-store objects under nhdw-exports/
--                  with full provenance: who, when, which marts, which
--                  filters, what policy decision allowed it, sha256,
--                  retention TTL, citation blurb. The record of what data
--                  left the warehouse.
--
--   ops_artifact   Workshop dashboard's notebook/model/dataset catalogue.
--                  Versioned, peer-reviewed, citation-tracked.
--                  Bytes live in the nhdw-artifacts object-store bucket.
--
--   ops_audit      Every gateway query, every OPA decision, every
--                  user-visible data event. Append-only.
--
-- Convention: every table has an `id` PK (uuid where the row is publicly
-- referenced, bigserial where it is only an internal log row), and ts/at
-- columns are `timestamptz` with `default now()`.
--
-- Foreign keys to ops_identity.user use kc_sub (the Keycloak subject UUID)
-- as the natural key. It survives Keycloak realm migrations and lets rows
-- reference a user who has not logged in yet (a steward can pre-create an
-- access decision for a kc_sub that has no JIT row).

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- ops_identity
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops_identity;
COMMENT ON SCHEMA ops_identity IS
    'JIT-cached projection of Keycloak users/groups. Refreshed on each login. NEVER authoritative.';

CREATE TABLE IF NOT EXISTS ops_identity.user (
    kc_sub          uuid        PRIMARY KEY,
    realm           text        NOT NULL,
    username        text        NOT NULL,
    email           text,
    full_name       text,
    role_label      text,                                   -- denormalised primary role for fast UI rendering
    locale          text        DEFAULT 'en',
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    disabled_at     timestamptz,                            -- shadow ban; KC remains source of truth
    UNIQUE (realm, username)
);
COMMENT ON TABLE ops_identity.user IS
    'One row per distinct Keycloak subject. Upsert on every successful login via the gateway''s /me handler.';

CREATE TABLE IF NOT EXISTS ops_identity.user_scope (
    kc_sub          uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    kind            text        NOT NULL CHECK (kind IN ('geography', 'programme', 'facility')),
    value           text        NOT NULL,                   -- region_id / programme_id / facility_id / mart_name
    granted_at      timestamptz NOT NULL DEFAULT now(),
    granted_by      uuid        REFERENCES ops_identity.user (kc_sub),
    expires_at      timestamptz,
    note            text,
    PRIMARY KEY (kc_sub, kind, value)
);
COMMENT ON TABLE ops_identity.user_scope IS
    'Data scope the user is allowed to see. The gateway joins this on every query and the OPA policy reads from it (via context push).';

CREATE TABLE IF NOT EXISTS ops_identity.group (
    kc_group_id     uuid        PRIMARY KEY,
    realm           text        NOT NULL,
    name            text        NOT NULL,
    kind            text,                                   -- 'workshop' | 'department' | 'project'
    description     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (realm, name)
);

CREATE TABLE IF NOT EXISTS ops_identity.group_membership (
    kc_group_id     uuid        NOT NULL REFERENCES ops_identity.group (kc_group_id) ON DELETE CASCADE,
    kc_sub          uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    role_in_group   text        NOT NULL DEFAULT 'member' CHECK (role_in_group IN ('owner', 'admin', 'member')),
    joined_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kc_group_id, kc_sub)
);

CREATE TABLE IF NOT EXISTS ops_identity.session (
    jti             uuid        PRIMARY KEY,
    kc_sub          uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    issued_at       timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz,
    user_agent      text,
    ip              inet
);
CREATE INDEX IF NOT EXISTS idx_session_kc_sub ON ops_identity.session (kc_sub);
COMMENT ON TABLE ops_identity.session IS
    'Server-side session ledger for explicit logout / single-session enforcement / token revocation. Keycloak''s SSO session is the upstream truth.';

-- ---------------------------------------------------------------------------
-- ops_workspace
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops_workspace;
COMMENT ON SCHEMA ops_workspace IS
    'Per-user UI state: preferences, saved queries, saved dashboards, alert subscriptions.';

CREATE TABLE IF NOT EXISTS ops_workspace.pref (
    kc_sub          uuid        PRIMARY KEY REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    theme           text        DEFAULT 'light' CHECK (theme IN ('light', 'dark', 'auto')),
    language        text        DEFAULT 'en',
    default_period  text        DEFAULT 'last_12_months',
    pinned_widgets  jsonb       DEFAULT '[]'::jsonb,
    extras          jsonb       DEFAULT '{}'::jsonb,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops_workspace.saved_query (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    title           text        NOT NULL,
    description     text,
    sql_text        text,                                   -- raw SQL (gateway runs through OPA before exec)
    builder_spec    jsonb,                                  -- optional structured spec from query-builder UI
    mart_scope      text[]      NOT NULL DEFAULT '{}',      -- marts this query touches
    share_scope     text        NOT NULL DEFAULT 'private'  -- 'private' | 'group:<UUID>' | 'org'
                    CHECK (share_scope = 'private'
                        OR share_scope = 'org'
                        OR share_scope ~ '^group:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    last_run_at     timestamptz
);
CREATE INDEX IF NOT EXISTS idx_saved_query_owner ON ops_workspace.saved_query (owner);
CREATE INDEX IF NOT EXISTS idx_saved_query_share ON ops_workspace.saved_query (share_scope);

CREATE TABLE IF NOT EXISTS ops_workspace.saved_dashboard (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    title           text        NOT NULL,
    description     text,
    layout          jsonb       NOT NULL,                   -- widget IDs + grid positions
    share_scope     text        NOT NULL DEFAULT 'private',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_saved_dashboard_owner ON ops_workspace.saved_dashboard (owner);

CREATE TABLE IF NOT EXISTS ops_workspace.subscription (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    widget_signature text       NOT NULL,                   -- stable hash of (page_id, widget_id, filter_envelope)
    trigger_spec    jsonb       NOT NULL,                   -- {metric, op:'>=', threshold, lookback}
    channels        text[]      NOT NULL DEFAULT '{email}', -- 'email' | 'slack' | 'webhook'
    status          text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'expired')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_fired_at   timestamptz,
    fire_count      integer     NOT NULL DEFAULT 0,
    UNIQUE (owner, widget_signature)
);

-- One row per fired alert (evaluator DAG -> gateway/Mailpit). The topbar bell
-- and "My alerts" read these; owner-scoped like the subscription that spawned them.
CREATE TABLE IF NOT EXISTS ops_workspace.alert_notification (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id uuid        NOT NULL REFERENCES ops_workspace.subscription (id) ON DELETE CASCADE,
    owner           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    disease         text,                                   -- mart disease_code (CHOL/MEA/…) or 'all'
    geography       text,                                   -- mart region_id (AA/OR/…) or 'all'
    metric          text,
    metric_value    numeric,
    threshold       numeric,
    signal_period   integer,                                -- ISO year*100+week of the firing forecast
    message         text        NOT NULL,
    fired_at        timestamptz NOT NULL DEFAULT now(),
    read_at         timestamptz,
    emailed         boolean     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_alert_notification_owner
    ON ops_workspace.alert_notification (owner, fired_at DESC);
-- Idempotent evaluation: one notification per (subscription, disease, geography,
-- forecast week). Re-running the evaluator against the same ML run is a no-op
-- (INSERT … ON CONFLICT DO NOTHING); a fresh forecast week fires anew.
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_notification_dedup
    ON ops_workspace.alert_notification (subscription_id, disease, geography, signal_period);

-- Report templates. A template is a named, scoped view over a mart; generating
-- it runs the same OPA-gated export path (export.mart + row_filter scope),
-- renders XLSX/CSV/HTML to the object store and returns a presigned download.
-- owner NULL = a system/seeded template visible to everyone; a non-NULL owner
-- makes it private to that user (saved from the report builder).
CREATE TABLE IF NOT EXISTS ops_workspace.report_template (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner          uuid        REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    name           text        NOT NULL,
    description    text,
    category       text,
    schema_name    text        NOT NULL,            -- e.g. bi_idsr
    table_name     text        NOT NULL,            -- e.g. mart_idsr_disease_weekly
    columns        text[],                          -- NULL = all columns
    filters        jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- default WHERE (dim -> value)
    order_by       text[],
    row_limit      integer     NOT NULL DEFAULT 5000,
    default_format text        NOT NULL DEFAULT 'xlsx'
                   CHECK (default_format IN ('xlsx', 'csv', 'html')),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_report_template_owner
    ON ops_workspace.report_template (owner NULLS FIRST, created_at DESC);

-- Seeded system templates over real marts (fixed ids keep the insert idempotent).
INSERT INTO ops_workspace.report_template
    (id, owner, name, description, category, schema_name, table_name, default_format)
VALUES
  ('11111111-0000-4000-a000-000000000071', NULL,
   'Disease surveillance, weekly', 'IDSR new cases + deaths by disease and woreda, latest weeks.',
   'Surveillance', 'bi_idsr', 'mart_idsr_disease_weekly', 'xlsx'),
  ('11111111-0000-4000-a000-000000000072', NULL,
   'Programme service cascade', 'Programme cascade attainment (HIV/TB/MNCH/…) by geography.',
   'Programmes', 'bi_service', 'mart_programme_cascade', 'xlsx'),
  ('11111111-0000-4000-a000-000000000073', NULL,
   'Facility capacity & readiness', 'Facility-level capacity + service readiness snapshot.',
   'Facilities', 'bi_facility', 'mart_facility_capacity', 'xlsx'),
  ('11111111-0000-4000-a000-000000000074', NULL,
   'Supply resource allocation', 'Commodity resource allocation + stock position by region.',
   'Supply chain', 'bi_supply', 'mart_resource_allocation', 'csv')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- ops_governance
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops_governance;
COMMENT ON SCHEMA ops_governance IS
    'Access requests, approvals, and data-quality flag inbox.';

CREATE TABLE IF NOT EXISTS ops_governance.access_request (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    requester       uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    requested_role  text        NOT NULL,                   -- target OPA role label, e.g. 'R-CONS-ANA'
    requested_scope jsonb       NOT NULL DEFAULT '{}'::jsonb,
    justification   text        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'declined', 'info_requested', 'withdrawn')),
    reviewer        uuid        REFERENCES ops_identity.user (kc_sub),
    decision_at     timestamptz,
    decision_note   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz                             -- if pending, auto-expire after N days
);
CREATE INDEX IF NOT EXISTS idx_access_req_status ON ops_governance.access_request (status, created_at);
CREATE INDEX IF NOT EXISTS idx_access_req_requester ON ops_governance.access_request (requester);

CREATE TABLE IF NOT EXISTS ops_governance.dq_flag (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    raised_at       timestamptz NOT NULL DEFAULT now(),
    raised_by       text        NOT NULL DEFAULT 'system',  -- 'system' | <kc_sub::text>
    source          text        NOT NULL,                   -- 'dhis2' | 'hcmis' | 'mfr' | 'ethioemr' | 'mbrana' | 'cross'
    table_name      text        NOT NULL,
    key             jsonb       NOT NULL,                   -- primary-key projection of the offending row(s)
    error_class     text        NOT NULL,                   -- 'null_in_required' | 'outlier' | 'fk_orphan' | 'timeliness' | ...
    severity        text        NOT NULL DEFAULT 'warn' CHECK (severity IN ('info', 'warn', 'error', 'critical')),
    description     text        NOT NULL,
    status          text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acked', 'resolved', 'wontfix'))
);
CREATE INDEX IF NOT EXISTS idx_dq_flag_status ON ops_governance.dq_flag (status, raised_at);
CREATE INDEX IF NOT EXISTS idx_dq_flag_source ON ops_governance.dq_flag (source, table_name);

CREATE TABLE IF NOT EXISTS ops_governance.dq_ack (
    flag_id         uuid        NOT NULL REFERENCES ops_governance.dq_flag (id) ON DELETE CASCADE,
    acker           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    acked_at        timestamptz NOT NULL DEFAULT now(),
    note            text,
    PRIMARY KEY (flag_id, acker, acked_at)
);

-- ---------------------------------------------------------------------------
-- ops_export
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops_export;
COMMENT ON SCHEMA ops_export IS
    'Registry of every dataset a dashboard or gateway query generated. '
    'Bytes live under nhdw-exports/ in MinIO. THIS table is the authoritative '
    '"what data left the warehouse" log.';

CREATE TABLE IF NOT EXISTS ops_export.export_registry (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    requester         uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    requested_at      timestamptz NOT NULL DEFAULT now(),
    completed_at      timestamptz,
    mart_scope        text[]      NOT NULL DEFAULT '{}',    -- e.g. {mart_hmis_indicator_coverage}
    saved_query_id    uuid        REFERENCES ops_workspace.saved_query (id),
    sql_text          text,                                 -- the actual SQL the gateway ran
    filter_envelope   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    policy_decision   jsonb       NOT NULL,                 -- full OPA response (allow + obligations + rule_hits)
    row_count         bigint,
    byte_count        bigint,
    sha256            text,                                 -- of the rendered object
    minio_bucket      text        NOT NULL DEFAULT 'nhdw-exports',
    minio_key         text,                                 -- e.g. user=<sub>/yyyy/mm/dd/<id>.<fmt>
    format            text        NOT NULL CHECK (format IN ('csv', 'parquet', 'xlsx', 'pdf', 'json', 'html')),
    status            text        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'ready', 'expired', 'deleted', 'failed')),
    retention_ttl_days integer    NOT NULL DEFAULT 90,
    pinned_until      timestamptz,                          -- NULL = follow TTL; non-NULL = keep until this date
    -- expires_at is maintained by trg_export_expires_at on INSERT/UPDATE.
    -- It can't be a GENERATED column because `(int || 'days')::interval` is
    -- not marked IMMUTABLE in Postgres; a trigger gives us the same end-state.
    expires_at        timestamptz,
    citation_blurb    text,                                 -- formatted citation for downstream re-use
    error_msg         text                                  -- if status='failed'
);
CREATE INDEX IF NOT EXISTS idx_export_requester ON ops_export.export_registry (requester, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_export_status    ON ops_export.export_registry (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_export_mart      ON ops_export.export_registry USING gin (mart_scope);

CREATE TABLE IF NOT EXISTS ops_export.export_lineage (
    export_id           uuid    NOT NULL REFERENCES ops_export.export_registry (id) ON DELETE CASCADE,
    parent_export_id    uuid    REFERENCES ops_export.export_registry (id),
    derived_kind        text    NOT NULL CHECK (derived_kind IN ('rerun', 'filter_narrow', 'format_convert', 'manual')),
    note                text,
    PRIMARY KEY (export_id, parent_export_id)
);

-- ---------------------------------------------------------------------------
-- ops_artifact
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops_artifact;
COMMENT ON SCHEMA ops_artifact IS
    'Workshop dashboard catalogue: user-uploaded notebooks, models, datasets. '
    'Bytes live in nhdw-artifacts/. Versioned + peer-reviewed + citation-tracked.';

CREATE TABLE IF NOT EXISTS ops_artifact.workshop_group (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text        NOT NULL,
    description     text,
    owner           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    kc_group_id     uuid        REFERENCES ops_identity.group (kc_group_id),
    visibility      text        NOT NULL DEFAULT 'open' CHECK (visibility IN ('open', 'request', 'closed')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS ops_artifact.workshop_group_member (
    group_id        uuid        NOT NULL REFERENCES ops_artifact.workshop_group (id) ON DELETE CASCADE,
    kc_sub          uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    role            text        NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    joined_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, kc_sub)
);

CREATE TABLE IF NOT EXISTS ops_artifact.artifact (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner           uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    kind            text        NOT NULL CHECK (kind IN ('notebook', 'dataset', 'model', 'report', 'pipeline')),
    title           text        NOT NULL,
    description     text,
    license         text        NOT NULL DEFAULT 'CC-BY-4.0',
    tier            text        NOT NULL DEFAULT 'personal' CHECK (tier IN ('public', 'shared', 'personal')),
    workshop_group  uuid        REFERENCES ops_artifact.workshop_group (id),
    citation        text,
    tags            text[]      NOT NULL DEFAULT '{}',
    peer_review_status text     NOT NULL DEFAULT 'unreviewed'
                    CHECK (peer_review_status IN ('unreviewed', 'in_review', 'approved', 'rejected')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_artifact_owner ON ops_artifact.artifact (owner);
CREATE INDEX IF NOT EXISTS idx_artifact_tier  ON ops_artifact.artifact (tier);
CREATE INDEX IF NOT EXISTS idx_artifact_kind  ON ops_artifact.artifact (kind);
CREATE INDEX IF NOT EXISTS idx_artifact_tags  ON ops_artifact.artifact USING gin (tags);

CREATE TABLE IF NOT EXISTS ops_artifact.artifact_version (
    artifact_id     uuid        NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    version         integer     NOT NULL,
    minio_bucket    text        NOT NULL DEFAULT 'nhdw-artifacts',
    minio_key       text        NOT NULL,
    sha256          text        NOT NULL,
    byte_count      bigint      NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    change_note     text,
    PRIMARY KEY (artifact_id, version)
);

CREATE TABLE IF NOT EXISTS ops_artifact.artifact_provenance (
    id                     bigserial PRIMARY KEY,
    artifact_id            uuid NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    parent_artifact_id     uuid REFERENCES ops_artifact.artifact (id),
    parent_export_id       uuid REFERENCES ops_export.export_registry (id),
    parent_mart_name       text,
    note                   text
);
-- Uniqueness across the nullable parent triplet, treating NULL as a stable sentinel.
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_provenance ON ops_artifact.artifact_provenance
    (artifact_id,
     COALESCE(parent_artifact_id, '00000000-0000-0000-0000-000000000000'::uuid),
     COALESCE(parent_export_id,   '00000000-0000-0000-0000-000000000000'::uuid),
     COALESCE(parent_mart_name, ''));

CREATE TABLE IF NOT EXISTS ops_artifact.artifact_review (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id     uuid        NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    reviewer        uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    verdict         text        NOT NULL CHECK (verdict IN ('approved', 'needs_changes', 'rejected', 'pending')),
    comment         text,
    reviewed_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, reviewer)
);

CREATE TABLE IF NOT EXISTS ops_artifact.artifact_metric (
    artifact_id     uuid        PRIMARY KEY REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    views           bigint      NOT NULL DEFAULT 0,
    downloads       bigint      NOT NULL DEFAULT 0,
    likes           bigint      NOT NULL DEFAULT 0,
    last_event_at   timestamptz NOT NULL DEFAULT now()
);

-- Per-user like ledger: the source of truth behind artifact_metric.likes.
-- One row per (artifact, user): the gateway toggles the row and recomputes
-- the aggregate inside the same transaction, so a like is idempotent per
-- user and the counter self-heals instead of drifting.
CREATE TABLE IF NOT EXISTS ops_artifact.artifact_like (
    artifact_id     uuid        NOT NULL REFERENCES ops_artifact.artifact (id) ON DELETE CASCADE,
    kc_sub          uuid        NOT NULL REFERENCES ops_identity.user (kc_sub),
    liked_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, kc_sub)
);

-- ---------------------------------------------------------------------------
-- ops_audit  (append-only: no DELETE permissions granted)
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops_audit;
COMMENT ON SCHEMA ops_audit IS
    'Append-only log of gateway queries, OPA decisions, and user-visible data events. Source of truth for the Admin Audit timeline.';

CREATE TABLE IF NOT EXISTS ops_audit.gateway_log (
    id              bigserial   PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT now(),
    kc_sub          uuid        REFERENCES ops_identity.user (kc_sub),
    jwt_jti         uuid,
    method          text        NOT NULL,                   -- GET | POST | ...
    path            text        NOT NULL,                   -- /api/marts/<name>?...
    mart_scope      text[]      NOT NULL DEFAULT '{}',
    filters         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    row_count       bigint,
    byte_count      bigint,
    latency_ms      integer,
    http_status     integer,
    policy_decision text        NOT NULL DEFAULT 'allow' CHECK (policy_decision IN ('allow', 'deny', 'error')),
    policy_obligations jsonb,
    denied_reason   text,
    client_ip       inet,
    user_agent      text
);
CREATE INDEX IF NOT EXISTS idx_gateway_log_ts      ON ops_audit.gateway_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_gateway_log_kc_sub  ON ops_audit.gateway_log (kc_sub, ts DESC);
CREATE INDEX IF NOT EXISTS idx_gateway_log_mart    ON ops_audit.gateway_log USING gin (mart_scope);

CREATE TABLE IF NOT EXISTS ops_audit.policy_log (
    id              bigserial   PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT now(),
    kc_sub          uuid        REFERENCES ops_identity.user (kc_sub),
    action          text        NOT NULL,                   -- 'read.mart' | 'export.mart' | 'publish.artifact' | ...
    resource        text        NOT NULL,                   -- mart name / artifact id / export id
    decision        text        NOT NULL CHECK (decision IN ('allow', 'deny')),
    rule_hits       text[]      NOT NULL DEFAULT '{}',      -- OPA rule paths that fired
    explanation     jsonb,
    correlation_id  uuid                                    -- ties policy_log row to its gateway_log row
);
CREATE INDEX IF NOT EXISTS idx_policy_log_ts ON ops_audit.policy_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_policy_log_kc_sub ON ops_audit.policy_log (kc_sub, ts DESC);

CREATE TABLE IF NOT EXISTS ops_audit.data_event (
    id              bigserial   PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT now(),
    kc_sub          uuid        REFERENCES ops_identity.user (kc_sub),
    kind            text        NOT NULL,                   -- 'export.created' | 'export.downloaded' | 'artifact.published' | 'subscription.fired' | ...
    payload         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    correlation_id  uuid
);
CREATE INDEX IF NOT EXISTS idx_data_event_ts ON ops_audit.data_event (ts DESC);
CREATE INDEX IF NOT EXISTS idx_data_event_kind ON ops_audit.data_event (kind, ts DESC);

-- ---------------------------------------------------------------------------
-- Triggers: touch updated_at on mutations.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ops_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pref_touch ON ops_workspace.pref;
CREATE TRIGGER trg_pref_touch BEFORE UPDATE ON ops_workspace.pref
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();

DROP TRIGGER IF EXISTS trg_saved_query_touch ON ops_workspace.saved_query;
CREATE TRIGGER trg_saved_query_touch BEFORE UPDATE ON ops_workspace.saved_query
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();

DROP TRIGGER IF EXISTS trg_saved_dashboard_touch ON ops_workspace.saved_dashboard;
CREATE TRIGGER trg_saved_dashboard_touch BEFORE UPDATE ON ops_workspace.saved_dashboard
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();

DROP TRIGGER IF EXISTS trg_artifact_touch ON ops_artifact.artifact;
CREATE TRIGGER trg_artifact_touch BEFORE UPDATE ON ops_artifact.artifact
    FOR EACH ROW EXECUTE FUNCTION ops_touch_updated_at();

-- Maintain ops_export.export_registry.expires_at on INSERT/UPDATE.
CREATE OR REPLACE FUNCTION ops_export_compute_expires_at() RETURNS trigger AS $$
BEGIN
    NEW.expires_at := COALESCE(NEW.completed_at, NEW.requested_at)
                      + (NEW.retention_ttl_days * interval '1 day');
    RETURN NEW;
END$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_export_expires_at ON ops_export.export_registry;
CREATE TRIGGER trg_export_expires_at BEFORE INSERT OR UPDATE OF completed_at, requested_at, retention_ttl_days
    ON ops_export.export_registry
    FOR EACH ROW EXECUTE FUNCTION ops_export_compute_expires_at();
