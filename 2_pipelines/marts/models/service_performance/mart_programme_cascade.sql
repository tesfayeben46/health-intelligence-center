/*
    mart_programme_cascade: programme funnel attainment (annual by region).

    Computes cascade steps per programme by aggregating the appropriate
    HMIS indicators (from mart_hmis_indicator_coverage). The cascade map
    owns only the funnel structure (which indicator is which step, keyed
    on the seeded DHIS2 element codes; see data_elements);
    programme membership rides in from the parent mart, which reads it
    from v_programme_resolution, the business vault's single source of
    truth for the indicator -> programme mapping:

      HIV   -> HIV-TEST -> HIV-POS -> HIV-ART-NEW -> HIV-VL-SUPP
      TB    -> TB-NOTIF -> TB-TX-NEW -> TB-TX-SUCC          (3-step funnel)
      MNCH  -> ANC1 -> ANC4 -> SBA -> PNC1
      NCD   -> HTN-SCRN -> HTN-DX -> HTN-TX -> HTN-CTRL

    Every step carries its measurement kind (entity-model.yaml is the
    authority: calc + unit per indicator), because summing percentage
    indicators as if they were counts breaks funnel monotonicity:
      - flow        count element   -> annual SUM (flows add over time)
      - stock       count element   -> annual AVG of monthly totals (a stock
                    summed across months counts the same patients 12 times;
                    HTN-TX "on treatment" is a prevalent stock)
      - cohort_pct  pct_of element  -> effective count = pct by the previous
                    step's value (cohort semantics: "of those who reached
                    the previous step, X% achieved this one"; matches how
                    TB-TX-SUCC / HIV-VL-SUPP / HTN-CTRL are reported)

    Funnel drop-off = (1 - step_n / step_1) * 100.
*/
WITH cascade_map (indicator_id, step_order, step_kind) AS (
    VALUES
        ('HIV-TEST',    1, 'flow'),
        ('HIV-POS',     2, 'flow'),
        ('HIV-ART-NEW', 3, 'flow'),
        ('HIV-VL-SUPP', 4, 'cohort_pct'),
        ('TB-NOTIF',    1, 'flow'),
        ('TB-TX-NEW',   2, 'flow'),
        ('TB-TX-SUCC',  3, 'cohort_pct'),
        ('ANC1',        1, 'flow'),
        ('ANC4',        2, 'flow'),
        ('SBA',         3, 'flow'),
        ('PNC1',        4, 'flow'),
        ('HTN-SCRN',    1, 'flow'),
        ('HTN-DX',      2, 'flow'),
        ('HTN-TX',      3, 'stock'),
        ('HTN-CTRL',    4, 'cohort_pct')
),
monthly AS (
    -- Regional monthly totals (sum across woredas is correct for counts and
    -- stocks, since patients at different facilities add up in space) plus the
    -- mean reported value (the appropriate aggregate for percentage elements).
    SELECT
        region_id,
        programme_id,
        SUBSTRING(month_id FROM 1 FOR 4) AS year_id,
        month_id,
        indicator_id,
        SUM(total_value) AS month_total,
        AVG(mean_value)  AS month_mean
    FROM {{ ref('mart_hmis_indicator_coverage') }}
    GROUP BY 1,2,3,4,5
),
base AS (
    SELECT
        m.region_id,
        m.programme_id,
        m.year_id,
        m.indicator_id,
        cm.step_order,
        cm.step_kind,
        SUM(m.month_total) AS flow_total,
        AVG(m.month_total) AS stock_avg,
        AVG(m.month_mean)  AS pct_avg
    FROM monthly m
    JOIN cascade_map cm USING (indicator_id)
    GROUP BY 1,2,3,4,5,6
),
direct AS (
    SELECT *,
        CASE step_kind
            WHEN 'flow'  THEN flow_total
            WHEN 'stock' THEN stock_avg
        END AS direct_value
    FROM base
),
resolved AS (
    -- cohort_pct steps convert to an effective count against the previous
    -- step (clamped to 0..100 first). Only terminal steps are pct in every
    -- funnel above, so LAG always lands on a directly-valued step.
    SELECT
        region_id, programme_id, year_id, step_order, indicator_id, step_kind,
        CASE WHEN step_kind = 'cohort_pct'
             THEN ROUND(
                    (LEAST(100.0, GREATEST(0.0, pct_avg)) / 100.0)
                    * COALESCE(LAG(direct_value) OVER (
                        PARTITION BY region_id, programme_id, year_id
                        ORDER BY step_order), 0))
             ELSE ROUND(direct_value)
        END AS value_total
    FROM direct
)
SELECT
    region_id,
    programme_id,
    year_id,
    step_order,
    indicator_id AS step_indicator,
    value_total,
    CASE WHEN step_order > 1
         THEN ROUND( 100.0 * value_total::numeric
              / NULLIF(MAX(value_total) FILTER (WHERE step_order = 1) OVER
                       (PARTITION BY region_id, programme_id, year_id), 0),
              1)
         ELSE 100.0
    END AS retention_pct_vs_entry
FROM resolved
