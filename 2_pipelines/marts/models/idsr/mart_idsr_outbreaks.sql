/*
    mart_idsr_outbreaks: outbreak episodes per (region by disease), derived
    from the weekly IDSR surveillance fact with an EARS-style threshold.

    Method (standard aberration detection, region grain):
      1. Aggregate mart_idsr_disease_weekly to region by disease by week.
      2. Baseline per week = mean + 2·stddev of the preceding 8 weeks
         (same region by disease; weeks with <8 prior weeks never flag).
      3. A week is in outbreak when new_cases exceeds
         GREATEST(baseline, per-disease floor); the floor stops 2-case
         "outbreaks" of rare diseases from flagging on noise.
      4. Consecutive flagged weeks collapse into one episode (islands via
         the week_start - rank·7d trick). Episode metrics: totals, CFR,
         peak week; status = 'active' while the episode's last flagged week
         is within 14 days of the newest surveillance week, else 'contained'.

    Consumed by: dz-outbreaks-table (Internal BI · Disease Surveillance).

    ML enrichment (predicted trajectory / alarm probability per episode from
    ops_ml.ml_predictions) attaches to this surveillance-derived episode list,
    which is its stable spine.
*/
WITH weekly AS (
    SELECT
        region_id,
        disease_code,
        MAX(disease_label) AS disease_label,
        iso_week,
        week_start,
        SUM(new_cases) AS new_cases,
        SUM(deaths)    AS deaths
    FROM {{ ref('mart_idsr_disease_weekly') }}
    GROUP BY region_id, disease_code, iso_week, week_start
),
floors (disease_code, floor_cases) AS (
    -- Minimum weekly cases before a threshold breach may flag. Rare/severe
    -- immediately-notifiable diseases keep a low floor.
    VALUES ('MAL', 30), ('TB', 20), ('MEN', 10), ('TYPH', 10), ('MEA', 10),
           ('CHOL', 5), ('DENG', 10), ('RELF', 10), ('NNT', 3), ('YF', 3)
),
baselined AS (
    SELECT
        w.*,
        COUNT(*)        OVER prev8 AS prior_weeks,
        AVG(new_cases)  OVER prev8 AS base_mean,
        STDDEV_SAMP(new_cases) OVER prev8 AS base_sd
    FROM weekly w
    WINDOW prev8 AS (
        PARTITION BY region_id, disease_code
        ORDER BY week_start
        ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING
    )
),
flagged AS (
    SELECT
        b.*,
        ROUND(GREATEST(b.base_mean + 2 * COALESCE(b.base_sd, 0),
                       f.floor_cases)::numeric, 1) AS threshold
    FROM baselined b
    JOIN floors f USING (disease_code)
    WHERE b.prior_weeks >= 8
      AND b.new_cases > GREATEST(b.base_mean + 2 * COALESCE(b.base_sd, 0),
                                 f.floor_cases)
),
islands AS (
    -- Consecutive flagged weeks share one group key.
    SELECT
        f.*,
        f.week_start
          - (ROW_NUMBER() OVER (PARTITION BY region_id, disease_code
                                ORDER BY week_start) * 7)::int AS episode_key
    FROM flagged f
),
latest AS (
    SELECT MAX(week_start) AS max_week FROM weekly
),
episodes AS (
    SELECT
        region_id,
        disease_code,
        MAX(disease_label)             AS disease_label,
        MIN(week_start)                AS declared_date,
        MIN(iso_week)                  AS declared_week,
        MAX(week_start)                AS last_flagged_date,
        MAX(iso_week)                  AS last_flagged_week,
        COUNT(*)                       AS weeks_active,
        SUM(new_cases)                 AS cases_total,
        SUM(deaths)                    AS deaths_total,
        MAX(new_cases)                 AS peak_week_cases,
        ROUND(AVG(threshold), 1)       AS avg_threshold
    FROM islands
    GROUP BY region_id, disease_code, episode_key
)
SELECT
    region_id || '-' || disease_code || '-' ||
      TO_CHAR(declared_date, 'IYYY"W"IW')          AS outbreak_id,
    region_id,
    disease_code,
    disease_label,
    declared_week,
    declared_date,
    last_flagged_week,
    last_flagged_date,
    weeks_active,
    cases_total,
    deaths_total,
    ROUND(100.0 * deaths_total / NULLIF(cases_total, 0), 2) AS cfr_pct,
    peak_week_cases,
    avg_threshold,
    CASE WHEN last_flagged_date >= (SELECT max_week FROM latest) - INTERVAL '14 days'
         THEN 'active' ELSE 'contained' END AS status
FROM episodes
ORDER BY declared_date DESC, cases_total DESC
