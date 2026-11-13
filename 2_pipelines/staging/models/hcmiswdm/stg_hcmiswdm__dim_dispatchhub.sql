/*
    EPSS dispatch-plan hub (warehouse) dimension: one row per hub_id.
    Natural key: hub_id. Coordinates + fleet size back the Supply Control Tower
    map. No source timestamp: idempotent full-replace reloads; the full-row
    fingerprint is the deterministic dedupe tie-breaker.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT hub_id, name, short_name, zone, lng, lat, is_hub, vehicles
    FROM {{ source('raw', 'hcmiswdm__dim_dispatchhub') }}
),
hashed AS (
    SELECT *, {{ source_record_hash(['hub_id','name','short_name','zone','lng','lat','is_hub','vehicles']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY hub_id ORDER BY source_record_hash) AS rn
    FROM hashed
)
SELECT hub_id, name, short_name, zone, lng, lat, is_hub, vehicles,
       now64() AS load_ts, source_record_hash
FROM deduped WHERE rn = 1
