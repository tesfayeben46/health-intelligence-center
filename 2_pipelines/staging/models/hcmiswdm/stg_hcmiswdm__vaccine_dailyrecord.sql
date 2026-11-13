/*
    HCMIS vaccine daily record: the vaccine-module fact.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT daily_record_id, date, item_id, unit_id, environment_id, received, issued, expired, soh
    FROM {{ source('raw', 'hcmiswdm__vaccine_dailyrecord') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['daily_record_id','date','item_id','unit_id','environment_id','received','issued','expired','soh']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY daily_record_id ORDER BY date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT daily_record_id, date, item_id, unit_id, environment_id, received, issued, expired, soh,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
