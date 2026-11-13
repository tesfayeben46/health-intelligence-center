/*
    mBrana stock-on-hand log: canonical fact, USSD-reported quantities by
    facility/commodity/date.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT soh_log_id, date, institution_id, commodity_id, quantity
    FROM {{ source('raw', 'mbrana__mflowussd_sohlogs') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['soh_log_id','date','institution_id','commodity_id','quantity']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY soh_log_id ORDER BY date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT soh_log_id, date, institution_id, commodity_id, quantity,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
