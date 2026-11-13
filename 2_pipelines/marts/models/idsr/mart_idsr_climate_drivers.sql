/*
    mart_idsr_climate_drivers: epi-week by region by disease by climate grain.

    Cross-tabs the weekly disease signal (mart_idsr_disease_weekly) against the
    weekly meteorological drivers (Climate raw vault: hub_climate_week +
    sat_climate_week) and computes:
      · rainfall / temperature anomalies as z-scores within each region,
      · the strongest lagged correlation between weekly cases and rainfall over
        candidate lags {0,2,4,8} weeks per region by disease.

    Climate flows through the full pipeline (source -> lake -> staging -> bridge
    -> raw vault), so this reads nhdw_vault_raw.

    Consumed by: Internal BI · Disease Surveillance (cases vs rainfall,
    climate correlation, z-score grid, risk bands) + the EWARS-lite climate model.
*/
WITH cases AS (
    SELECT iso_week, week_start, region_id, programme_id,
           disease_code, disease_label, SUM(new_cases) AS cases
    FROM {{ ref('mart_idsr_disease_weekly') }}
    GROUP BY 1, 2, 3, 4, 5, 6
),
climate AS (
    -- Satellite via the current-record view (insert-only sats; a bare sat
    -- join double-counts once history accumulates). The hub stays: it
    -- carries the region_id/iso_week business key.
    SELECT h.region_id,
           s.week_start::date       AS week_start,
           s.rainfall_mm::numeric  AS rainfall_mm,
           s.temp_celsius::numeric AS temp_celsius,
           s.humidity_pct::numeric AS humidity_pct
    FROM {{ source('nhdw_vault_raw', 'hub_climate_week') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_climate_week_current') }} s USING (hk_climate_week)
),
joined AS (
    SELECT c.iso_week, c.week_start, c.region_id, c.programme_id,
           c.disease_code, c.disease_label, c.cases,
           cl.rainfall_mm, cl.temp_celsius, cl.humidity_pct
    FROM cases c
    LEFT JOIN climate cl
      ON cl.region_id = c.region_id AND cl.week_start = c.week_start
),
anom AS (
    SELECT j.*,
        (rainfall_mm - AVG(rainfall_mm) OVER (PARTITION BY region_id))
            / NULLIF(STDDEV_POP(rainfall_mm) OVER (PARTITION BY region_id), 0)
            AS rainfall_anomaly_z,
        (temp_celsius - AVG(temp_celsius) OVER (PARTITION BY region_id))
            / NULLIF(STDDEV_POP(temp_celsius) OVER (PARTITION BY region_id), 0)
            AS temp_anomaly_z
    FROM joined j
),
lagged AS (
    SELECT a.region_id, a.disease_code, l.lag_weeks, a.cases,
           LAG(a.rainfall_mm, l.lag_weeks) OVER (
               PARTITION BY a.region_id, a.disease_code ORDER BY a.week_start
           ) AS rain_lag
    FROM anom a
    CROSS JOIN (SELECT UNNEST(ARRAY[0, 2, 4, 8]) AS lag_weeks) l
),
corr_by_lag AS (
    SELECT region_id, disease_code, lag_weeks, CORR(cases, rain_lag) AS c
    FROM lagged
    WHERE rain_lag IS NOT NULL
    GROUP BY 1, 2, 3
    HAVING CORR(cases, rain_lag) IS NOT NULL
),
best_lag AS (
    SELECT DISTINCT ON (region_id, disease_code)
           region_id, disease_code,
           lag_weeks AS best_lag_weeks, c AS best_corr
    FROM corr_by_lag
    ORDER BY region_id, disease_code, ABS(c) DESC
)
SELECT
    a.iso_week,
    a.week_start,
    a.region_id,
    a.programme_id,
    a.disease_label,
    a.cases,
    -- ROUND in Postgres is round(numeric, int); window/aggregate math (esp. CORR,
    -- which always returns double precision) yields doubles, so cast every arg to
    -- numeric everywhere, not only where a double leaks through.
    ROUND(a.rainfall_mm::numeric, 1)        AS rainfall_mm,
    ROUND(a.rainfall_anomaly_z::numeric, 3) AS rainfall_anomaly_z,
    ROUND(a.temp_celsius::numeric, 1)       AS temp_celsius,
    ROUND(a.temp_anomaly_z::numeric, 3)     AS temp_anomaly_z,
    ROUND(a.humidity_pct::numeric, 1)       AS humidity_pct,
    ROUND(b.best_corr::numeric, 3)          AS rainfall_lag_correlation,
    b.best_lag_weeks               AS lag_weeks
FROM anom a
LEFT JOIN best_lag b
  ON b.region_id = a.region_id AND b.disease_code = a.disease_code
