/*
    EPSS dispatch-plan vehicle-run fact: one row per run. Natural key: run_id.
    `period` is aliased to period_id to conform with the dispatch-period
    dimension. stops_json / path_json carry the nested per-run stop list and the
    road polyline as JSON strings (typed/parsed downstream in the vault/marts).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        run_id, period AS period_id, vehicle_type, capacity_units, origin_hub_id,
        total_road_km, total_units, fill_rate, cost_total, cost_per_unit,
        km_saved_vs_baseline, depart, eta, status, stops_json, path_json
    FROM {{ source('raw', 'hcmiswdm__fact_dispatchrun') }}
),
hashed AS (
    SELECT *, {{ source_record_hash(['run_id','period_id','vehicle_type','capacity_units','origin_hub_id','total_road_km','total_units','fill_rate','cost_total','cost_per_unit','km_saved_vs_baseline','depart','eta','status','stops_json','path_json']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY run_id ORDER BY source_record_hash) AS rn
    FROM hashed
)
SELECT
    run_id, period_id, vehicle_type, capacity_units, origin_hub_id,
    total_road_km, total_units, fill_rate, cost_total, cost_per_unit,
    km_saved_vs_baseline, depart, eta, status, stops_json, path_json,
    now64() AS load_ts, source_record_hash
FROM deduped WHERE rn = 1
