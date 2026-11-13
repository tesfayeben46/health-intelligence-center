-- Target-population census for elmis_consumption_forecast (docs/pct-forecast-contract.md, section 2).
-- Series = facility x tracer product (programmes summed); target = adjusted_demand
-- (bi_supply.mart_pct_demand_month: eLMIS normalised consumption, dispensed where NULL).
-- Window: the archive's 12 months; training cutoff for the final holdout = 2018-07.
-- Eligibility: >= 6 observed months before the holdout month and >= 1 non-zero demand.
-- Intermittency (Syntetos-Boylan): ADI = observed months / non-zero months,
-- CV² = variance / mean² of the non-zero demands; thresholds 1.32 and 0.49.
WITH monthly AS (
    SELECT facility_code, product_code, month_id,
           sum(adjusted_demand)   AS demand,
           bool_or(demand_censored) AS censored
    FROM bi_supply.mart_pct_demand_month
    WHERE tracer
    GROUP BY 1, 2, 3
),
series AS (
    SELECT facility_code, product_code,
           count(*) FILTER (WHERE month_id < '2018-08' AND demand IS NOT NULL)        AS obs_train,
           count(*) FILTER (WHERE month_id < '2018-08' AND demand > 0)                AS nonzero_train,
           count(*) FILTER (WHERE censored)                                           AS censored_months,
           count(*) FILTER (WHERE month_id = '2018-08' AND demand IS NOT NULL)        AS has_holdout,
           count(*) FILTER (WHERE month_id = '2018-08' AND censored)                  AS holdout_censored,
           avg(demand) FILTER (WHERE demand > 0)                                      AS nz_mean,
           var_samp(demand) FILTER (WHERE demand > 0)                                 AS nz_var,
           sum(demand)                                                                AS volume
    FROM monthly GROUP BY 1, 2
),
classified AS (
    SELECT s.*,
           CASE WHEN obs_train < 6   THEN 'excluded: fewer than 6 training months'
                WHEN nonzero_train = 0 THEN 'excluded: no non-zero demand'
                ELSE 'eligible' END                                                   AS eligibility,
           CASE WHEN nonzero_train > 0 THEN obs_train::numeric / nonzero_train END    AS adi,
           CASE WHEN nonzero_train > 1 AND nz_mean > 0 THEN nz_var / (nz_mean * nz_mean) END AS cv2
    FROM series s
)
SELECT eligibility,
       CASE WHEN eligibility <> 'eligible' THEN '-'
            WHEN adi < 1.32 AND coalesce(cv2, 0) < 0.49 THEN 'smooth'
            WHEN adi < 1.32 THEN 'erratic'
            WHEN coalesce(cv2, 0) < 0.49 THEN 'intermittent'
            ELSE 'lumpy' END                                                          AS demand_class,
       count(*)                                                                       AS series,
       round(sum(volume))                                                             AS volume,
       sum(has_holdout)                                                               AS with_aug_actual,
       sum(holdout_censored)                                                          AS aug_actual_censored,
       round(avg(censored_months), 2)                                                 AS avg_censored_months
FROM classified
GROUP BY 1, 2 ORDER BY 1, 2;
