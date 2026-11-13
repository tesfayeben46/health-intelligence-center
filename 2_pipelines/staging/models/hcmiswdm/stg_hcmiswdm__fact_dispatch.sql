/*
    EPSS dispatch-plan movement fact: one row per movement line (which hub
    supplies which facility, of which commodity, on which vehicle run, in which
    period). Natural key: movement_id. `period` is aliased to period_id so the
    period FK conforms with the dispatch-period dimension. why_json holds the
    nested demand-signal breakdown as a JSON string (typed/parsed downstream).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        movement_id, period AS period_id, from_hub_id, to_node_id,
        drug, drug_short, commodity_key, units, amc, mos, days_to_stockout, priority,
        lane_km, cost_units, sequence_index, vehicle_run_id, fill_rate,
        nearest_hub_id, alt_hub_id, reason, assign_delta_km, assign_delta_cost,
        baseline_hub_id, baseline_km, planned, repaired, capped, why_json
    FROM {{ source('raw', 'hcmiswdm__fact_dispatch') }}
),
hashed AS (
    SELECT *, {{ source_record_hash(['movement_id','period_id','from_hub_id','to_node_id','drug','drug_short','commodity_key','units','amc','mos','days_to_stockout','priority','lane_km','cost_units','sequence_index','vehicle_run_id','fill_rate','nearest_hub_id','alt_hub_id','reason','assign_delta_km','assign_delta_cost','baseline_hub_id','baseline_km','planned','repaired','capped','why_json']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY movement_id ORDER BY source_record_hash) AS rn
    FROM hashed
)
SELECT
    movement_id, period_id, from_hub_id, to_node_id,
    drug, drug_short, commodity_key, units, amc, mos, days_to_stockout, priority,
    lane_km, cost_units, sequence_index, vehicle_run_id, fill_rate,
    nearest_hub_id, alt_hub_id, reason, assign_delta_km, assign_delta_cost,
    baseline_hub_id, baseline_km, planned, repaired, capped, why_json,
    now64() AS load_ts, source_record_hash
FROM deduped WHERE rn = 1
