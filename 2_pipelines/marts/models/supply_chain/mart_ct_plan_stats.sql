{{ config(materialized='table') }}
{#
    Supply Control Tower plan summary (bi_supply.mart_ct_plan_stats), Zambia
    eLMIS, one row per planning period (the month after the latest reported
    month). Distances are straight-line (runs vs one trip per facility); no cost,
    capacity or fill data exists (NULL). uncovered_needs = facility-products at
    risk next month with no planned order; at_risk_facilities = facilities with a
    tracer product likely short or a critical tracer at risk (mart_pct_position).
#}

WITH mv AS (
    SELECT * FROM {{ ref('mart_ct_movements') }} WHERE planned
),

runs AS (
    SELECT * FROM {{ ref('mart_ct_vehicle_runs') }}
),

pos AS (
    SELECT facility_code, product_code,
           to_char((max(as_of_month) || '-01')::date + interval '1 month', 'YYYY-MM') AS period_id,
           bool_or(h1_risk IN ('likely_short', 'critical_at_risk'))       AS at_risk,
           coalesce(sum(planning_replenishment_units_excl_pipeline), 0)    AS units,
           max(max_stock_months)                                           AS max_months
    FROM {{ ref('mart_pct_position') }}
    GROUP BY facility_code, product_code
),

risk AS (
    SELECT period_id,
           count(*) FILTER (WHERE at_risk AND units <= 0)     AS uncovered_needs,
           count(DISTINCT facility_code) FILTER (WHERE at_risk) AS at_risk_facilities,
           max(max_months)                                     AS max_months
    FROM pos GROUP BY period_id
),

m AS (
    SELECT period_id, count(*) AS movements, sum(units) AS units,
           count(DISTINCT to_node_id) AS facilities, sum(2 * baseline_km) AS baseline_km
    FROM (SELECT period_id, to_node_id, sum(units) AS units, max(baseline_km) AS baseline_km, count(*) AS n
          FROM mv GROUP BY period_id, to_node_id) f
    GROUP BY period_id
),

mm AS (
    SELECT period_id, count(*) AS movements FROM mv GROUP BY period_id
),

r AS (
    SELECT period_id, count(*) AS runs, sum(total_road_km) AS optimized_km FROM runs GROUP BY period_id
)

SELECT
    m.period_id,
    to_char((m.period_id || '-01')::date, 'FMMonth YYYY')      AS label,
    (row_number() OVER (ORDER BY m.period_id))::int              AS sort_order,
    mm.movements::int                                            AS movements,
    m.units::bigint                                              AS units,
    r.runs::int                                                  AS runs,
    m.facilities::int                                            AS facilities,
    NULL::numeric                                                AS avg_fill,
    0                                                            AS overflow_legs,
    NULL::int                                                    AS min_months,
    risk.max_months::int                                         AS max_months,
    risk.uncovered_needs::int                                    AS uncovered_needs,
    risk.at_risk_facilities::int                                 AS at_risk_facilities,
    r.optimized_km::bigint                                       AS optimized_km,
    m.baseline_km::bigint                                        AS baseline_km,
    (m.baseline_km - r.optimized_km)::bigint                     AS km_saved,
    CASE WHEN m.baseline_km > 0 THEN round(100.0 * (m.baseline_km - r.optimized_km) / m.baseline_km) END::int AS km_saved_pct,
    NULL::bigint                                                 AS optimized_cost,
    NULL::bigint                                                 AS baseline_cost,
    NULL::bigint                                                 AS cost_saved,
    NULL::int                                                    AS cost_saved_pct,
    r.runs::int                                                  AS vehicles_used,
    m.facilities::int                                            AS naive_vehicles,
    (m.facilities - r.runs)::int                                 AS vehicles_saved
FROM m
JOIN mm USING (period_id)
JOIN r USING (period_id)
LEFT JOIN risk USING (period_id)
