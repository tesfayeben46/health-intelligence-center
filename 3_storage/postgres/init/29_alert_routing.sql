-- Admin-managed alert routing rules.
--
-- The admin dashboard's "Alert routing" section does CRUD against this table
-- through /api/alerts/routing (GET list, POST/PATCH/DELETE with the ops-admin
-- capability). A rule maps an alert selector
-- (severity + a match_label like `service=gateway` or `kind=dq.test`, matched
-- against the labels on a Prometheus/Alertmanager alert) to a delivery channel
-- and recipient.
--
-- Reboot-persistence follows the ops_governance.public_article pattern: the six
-- platform defaults are seeded here (managed_by='seed', idempotent upsert that
-- never overwrites a row an operator has edited); runtime edits (managed_by=
-- 'runtime') survive a pack because pg_dump of nhdw_ops captures the table.

\connect nhdw_ops

CREATE TABLE IF NOT EXISTS ops_governance.alert_routing (
    id           text        PRIMARY KEY DEFAULT ('ar-' || gen_random_uuid()::text),
    severity     text        NOT NULL,                    -- critical | warning | info
    match_label  text        NOT NULL,                    -- e.g. service=gateway, kind=dq.test
    channel      text        NOT NULL,                    -- pagerduty:… | slack:#… | email:…
    recipient    text        NOT NULL,
    note         text        NOT NULL DEFAULT '',
    enabled      boolean     NOT NULL DEFAULT true,
    managed_by   text        NOT NULL DEFAULT 'seed',     -- 'seed' | 'runtime' (reconciliation provenance)
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT alert_routing_severity_chk
        CHECK (severity IN ('critical','warning','info'))
);

CREATE INDEX IF NOT EXISTS ix_alert_routing_severity
    ON ops_governance.alert_routing (severity);

-- Six platform defaults. ON CONFLICT
-- keeps them current on a cold-boot re-seed but leaves operator edits intact:
-- a seed row only refreshes another seed row (managed_by stays 'seed').
INSERT INTO ops_governance.alert_routing
    (id, severity, match_label, channel, recipient, note, managed_by)
VALUES
    ('ar-gw-5xx',    'critical', 'service=gateway',
        'pagerduty:nhdw-oncall', 'ops-oncall@moh.gov.et', 'Gateway 5xx > 5/min for 2m', 'seed'),
    ('ar-db-down',   'critical', 'service=postgres-vault',
        'pagerduty:nhdw-oncall', 'ops-oncall@moh.gov.et', 'DB primary unreachable', 'seed'),
    ('ar-mlflow-lat','warning',  'service=mlflow',
        'slack:#nhdw-platform', '@platform', 'ML registry latency > p95 SLA', 'seed'),
    ('ar-loki-lag',  'warning',  'service=loki',
        'slack:#nhdw-platform', '@platform', 'Log ingestion lag > 30s', 'seed'),
    ('ar-bi-lat',    'info',     'service=internal-bi',
        'email:platform@moh.gov.et', 'platform@moh.gov.et', 'Dashboard latency > p95 SLA', 'seed'),
    ('ar-dq-tests',  'info',     'kind=dq.test',
        'email:stewards@moh.gov.et', 'stewards@moh.gov.et', 'dbt test failures (rolled up daily)', 'seed'),
    ('ar-dq-incidents', 'warning', 'kind=dq.incident',
        'inapp:dq-queue', 'admin › Data Quality', 'Detector incidents land in the in-app DQ queue only (no email by design)', 'seed')
ON CONFLICT (id) DO UPDATE SET
    severity    = EXCLUDED.severity,
    match_label = EXCLUDED.match_label,
    channel     = EXCLUDED.channel,
    recipient   = EXCLUDED.recipient,
    note        = EXCLUDED.note,
    updated_at  = now()
WHERE ops_governance.alert_routing.managed_by = 'seed';

DO $$
BEGIN
    -- The gateway connects as ops_gateway and performs the admin routing CRUD
    -- directly (same as it does for ops_workspace.subscription), so it needs
    -- full DML here, not just SELECT.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_governance.alert_routing TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_governance.alert_routing TO ops_writer;
    END IF;
END $$;
