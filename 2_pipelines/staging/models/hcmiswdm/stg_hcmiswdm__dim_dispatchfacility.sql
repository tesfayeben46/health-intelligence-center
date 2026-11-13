/*
    EPSS dispatch-plan delivery-facility dimension: one row per node_id.
    Natural key: node_id. This is the facility grain the Supply Control Tower
    needs (region / type / capacity / coordinates), finer than the
    hub/environment grain of the rest of the supply vault.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT node_id, name, region, facility_type, lng, lat, line_count, store_cap
    FROM {{ source('raw', 'hcmiswdm__dim_dispatchfacility') }}
),
hashed AS (
    SELECT *, {{ source_record_hash(['node_id','name','region','facility_type','lng','lat','line_count','store_cap']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY node_id ORDER BY source_record_hash) AS rn
    FROM hashed
)
SELECT node_id, name, region, facility_type, lng, lat, line_count, store_cap,
       now64() AS load_ts, source_record_hash
FROM deduped WHERE rn = 1
