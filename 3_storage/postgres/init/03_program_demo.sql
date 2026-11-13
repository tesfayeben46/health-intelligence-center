-- Programme demo schema: tables and per-programme views for the Internal-BI
-- detail components. The source table holds one numeric value per
-- region_code by program_code by period_month by metric; the views project it
-- into component shapes.
--
-- Schema only (tables, views, grants): applied by initdb and re-applied by
-- bundle/scripts/seed-app-security.sh (CREATE OR REPLACE, so it survives a
-- state restore). The demo rows (TRUNCATE + INSERT) live in the seed pack:
--   bundle/seed-pack/app/program-demo/program_demo_data.sql

CREATE SCHEMA IF NOT EXISTS bi_programme;

CREATE TABLE IF NOT EXISTS bi_facility.programme_metrics (
    region_code   text NOT NULL,
    program_code  text NOT NULL,            -- HIV | TB | MALARIA | EPI | MNCH
    period_month  text NOT NULL,            -- YYYY-MM
    metric        text NOT NULL,
    value         numeric NOT NULL,
    PRIMARY KEY (region_code, program_code, period_month, metric)
);


-- ingestion: submission funnel, row counts per source per status
CREATE TABLE IF NOT EXISTS bi_facility.submission_funnel (
    period_day text NOT NULL,
    source     text NOT NULL,
    status     text NOT NULL,
    rows       int  NOT NULL,
    PRIMARY KEY (period_day, source, status)
);

-- stock-out: facility by program by commodity, current status
CREATE TABLE IF NOT EXISTS bi_facility.stock_status (
    facility    text NOT NULL,
    region_code text NOT NULL,
    program_code text NOT NULL,
    drug        text NOT NULL,
    on_hand     int  NOT NULL,
    reorder_at  int  NOT NULL,
    status      text NOT NULL,
    PRIMARY KEY (facility, drug)
);

-- per-programme views
CREATE OR REPLACE VIEW bi_programme.hiv_cascade_v AS
WITH q AS (
  SELECT region_code, metric, SUM(value) AS v
  FROM bi_facility.programme_metrics
  WHERE program_code = 'HIV' AND period_month = '2026-Q1'
    AND metric IN ('diagnosed','on_treatment','suppressed')
  GROUP BY region_code, metric
)
SELECT region_code,
       SUM(v) FILTER (WHERE metric = 'diagnosed')      AS diagnosed,
       SUM(v) FILTER (WHERE metric = 'on_treatment')   AS on_treatment,
       SUM(v) FILTER (WHERE metric = 'suppressed')     AS suppressed
FROM q
GROUP BY region_code;

CREATE OR REPLACE VIEW bi_programme.hiv_cascade_total_v AS
SELECT 'Diagnosed'         AS stage, SUM(diagnosed)    AS n FROM bi_programme.hiv_cascade_v UNION ALL
SELECT 'On treatment',           SUM(on_treatment)            FROM bi_programme.hiv_cascade_v UNION ALL
SELECT 'Virally suppressed',     SUM(suppressed)              FROM bi_programme.hiv_cascade_v;

CREATE OR REPLACE VIEW bi_programme.hiv_incidence_trend_v AS
SELECT period_month AS period, region_code, SUM(value)::int AS new_diagnoses
FROM bi_facility.programme_metrics
WHERE program_code = 'HIV' AND metric = 'new_diagnoses'
GROUP BY period_month, region_code
ORDER BY period_month;

CREATE OR REPLACE VIEW bi_programme.tb_cascade_total_v AS
SELECT 'Notified'           AS stage, SUM(value)::int AS n FROM bi_facility.programme_metrics WHERE program_code='TB' AND metric='notified'           UNION ALL
SELECT 'On treatment',                SUM(value)::int        FROM bi_facility.programme_metrics WHERE program_code='TB' AND metric='on_treatment'        UNION ALL
SELECT 'Treatment success',           SUM(value)::int        FROM bi_facility.programme_metrics WHERE program_code='TB' AND metric='treatment_success';

CREATE OR REPLACE VIEW bi_programme.tb_mdr_by_region_v AS
SELECT region_code, SUM(value)::int AS mdr_cases
FROM bi_facility.programme_metrics
WHERE program_code='TB' AND metric='mdr_cases'
GROUP BY region_code ORDER BY mdr_cases DESC;

CREATE OR REPLACE VIEW bi_programme.malaria_by_region_v AS
WITH q AS (
  SELECT region_code, metric, SUM(value) AS v
  FROM bi_facility.programme_metrics
  WHERE program_code='MALARIA' AND period_month='2026-Q1'
  GROUP BY region_code, metric
)
SELECT region_code,
       SUM(v) FILTER (WHERE metric='suspected')::int  AS suspected,
       SUM(v) FILTER (WHERE metric='tested')::int     AS tested,
       SUM(v) FILTER (WHERE metric='confirmed')::int  AS confirmed
FROM q GROUP BY region_code ORDER BY confirmed DESC;

CREATE OR REPLACE VIEW bi_programme.malaria_trend_v AS
SELECT period_month AS period, region_code, SUM(value)::int AS confirmed
FROM bi_facility.programme_metrics
WHERE program_code='MALARIA' AND metric='confirmed'
  AND period_month LIKE '202_-__'
GROUP BY period_month, region_code
ORDER BY period_month;

CREATE OR REPLACE VIEW bi_programme.epi_coverage_v AS
WITH q AS (
  SELECT region_code, metric, AVG(value)::int AS v
  FROM bi_facility.programme_metrics
  WHERE program_code='EPI' AND period_month='2026-Q1'
  GROUP BY region_code, metric
)
SELECT region_code,
       SUM(v) FILTER (WHERE metric='dpt3_coverage_pct')    AS dpt3,
       SUM(v) FILTER (WHERE metric='measles_coverage_pct') AS measles,
       SUM(v) FILTER (WHERE metric='bcg_coverage_pct')     AS bcg
FROM q GROUP BY region_code ORDER BY region_code;

CREATE OR REPLACE VIEW bi_programme.mnch_coverage_v AS
WITH q AS (
  SELECT region_code, metric, AVG(value)::int AS v
  FROM bi_facility.programme_metrics
  WHERE program_code='MNCH' AND period_month='2026-Q1'
  GROUP BY region_code, metric
)
SELECT region_code,
       SUM(v) FILTER (WHERE metric='anc4_coverage_pct')    AS anc4,
       SUM(v) FILTER (WHERE metric='skilled_birth_pct')    AS skilled_birth,
       SUM(v) FILTER (WHERE metric='postnatal_pct')        AS postnatal
FROM q GROUP BY region_code ORDER BY region_code;

CREATE OR REPLACE VIEW bi_facility.submission_funnel_v AS
SELECT source AS label,
       SUM(rows) FILTER (WHERE status='accepted')::int AS accepted,
       SUM(rows) FILTER (WHERE status='rejected')::int AS rejected
FROM bi_facility.submission_funnel
GROUP BY source ORDER BY accepted DESC;

CREATE OR REPLACE VIEW bi_facility.stock_status_v AS
SELECT facility, region_code, program_code, drug, on_hand, reorder_at, status
FROM bi_facility.stock_status
ORDER BY CASE status WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, facility;

-- grants
GRANT USAGE  ON SCHEMA bi_facility, bi_programme TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA bi_facility, bi_programme TO bi_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA bi_facility, bi_programme
  GRANT SELECT ON TABLES TO bi_reader;
