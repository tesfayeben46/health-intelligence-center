-- PCT plan decision log (Supply Control Tower).
--
-- A planner approves or rejects a recommended plan line (one facility and
-- tracer product order for a planning period). The log is append-only and
-- bitemporal, like the MDM decision log (35): a new decision or a revert
-- stamps recorded_to on the current row and never updates a verdict in place.
--
-- Every decision snapshots the line as it was decided (units, sizing steps,
-- demand basis, forecast version and runs, policy), so a later
-- rebuild of the marts cannot change what was approved.
-- The warehouse reads this log through 43_pct_plan_read.sql.

\connect nhdw_ops

CREATE SCHEMA IF NOT EXISTS ops_plan;

CREATE TABLE IF NOT EXISTS ops_plan.plan_decision (
    decision_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_period         text        NOT NULL,                -- 'YYYY-MM' the plan is for
    movement_id         text        NOT NULL,                -- facility_code:product_code
    facility_code       text        NOT NULL,
    product_code        text        NOT NULL,
    from_hub_id         text        NOT NULL,
    decision            text        NOT NULL CHECK (decision IN ('approve','reject')),
    units_recommended   integer     NOT NULL,
    snapshot            jsonb       NOT NULL,                -- the line and its inputs as decided
    note                text,
    decided_by          text        NOT NULL,                -- kc username (audited via ops_audit too)
    recorded_from       timestamptz NOT NULL DEFAULT now(),
    recorded_to         timestamptz                          -- set on supersession or revert
);

COMMENT ON TABLE ops_plan.plan_decision IS
    'Append-only, bitemporal planner verdicts on Supply Control Tower plan lines; '
    'each row snapshots the line as decided. Current = recorded_to IS NULL.';

-- one current decision per plan line
CREATE UNIQUE INDEX IF NOT EXISTS plan_decision_current
    ON ops_plan.plan_decision (plan_period, movement_id)
    WHERE recorded_to IS NULL;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        -- the gateway records verdicts; supersession / revert = UPDATE of recorded_to only
        GRANT USAGE ON SCHEMA ops_plan TO ops_gateway;
        GRANT SELECT, INSERT, UPDATE ON ops_plan.plan_decision TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT USAGE ON SCHEMA ops_plan TO ops_reader;
        GRANT SELECT ON ops_plan.plan_decision TO ops_reader;
    END IF;
END $$;
