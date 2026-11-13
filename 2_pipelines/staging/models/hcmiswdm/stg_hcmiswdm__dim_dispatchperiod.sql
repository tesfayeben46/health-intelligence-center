/*
    EPSS dispatch-plan planning-period dimension: one row per period_id.
    Natural key: period_id. Carries the plan-level KPI/savings summary the
    solver computed for that period (units, runs, km/cost saved vs a naive
    one-truck-per-facility baseline). Backs the Control Tower KPI + savings card.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT period_id, label, sort_order,
           movements, units, runs, facilities, avg_fill, overflow_legs,
           min_months, max_months, uncovered_needs, at_risk_facilities,
           optimized_km, baseline_km, km_saved, km_saved_pct,
           optimized_cost, baseline_cost, cost_saved, cost_saved_pct,
           vehicles_used, naive_vehicles, vehicles_saved
    FROM {{ source('raw', 'hcmiswdm__dim_dispatchperiod') }}
),
hashed AS (
    SELECT *, {{ source_record_hash(['period_id','label','sort_order','movements','units','runs','facilities','avg_fill','overflow_legs','min_months','max_months','uncovered_needs','at_risk_facilities','optimized_km','baseline_km','km_saved','km_saved_pct','optimized_cost','baseline_cost','cost_saved','cost_saved_pct','vehicles_used','naive_vehicles','vehicles_saved']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY period_id ORDER BY source_record_hash) AS rn
    FROM hashed
)
SELECT period_id, label, sort_order,
       movements, units, runs, facilities, avg_fill, overflow_legs,
       min_months, max_months, uncovered_needs, at_risk_facilities,
       optimized_km, baseline_km, km_saved, km_saved_pct,
       optimized_cost, baseline_cost, cost_saved, cost_saved_pct,
       vehicles_used, naive_vehicles, vehicles_saved,
       now64() AS load_ts, source_record_hash
FROM deduped WHERE rn = 1
