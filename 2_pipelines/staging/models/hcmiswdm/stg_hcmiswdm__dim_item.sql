/*
    HCMIS item dimension: the analytics dimension for fact_dailyrecord.item_id.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT item_id, sn, full_item_name
    FROM {{ source('raw', 'hcmiswdm__dim_item') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['item_id','sn','full_item_name']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY item_id ORDER BY sn DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT item_id, sn, full_item_name,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
