/*
    mBrana mFlowUSSD commodity catalogue.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT commodity_id, commodity_code, commodity_name
    FROM {{ source('raw', 'mbrana__mflowussd_commodities') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['commodity_id','commodity_code','commodity_name']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY commodity_id ORDER BY commodity_name DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT commodity_id, commodity_code, commodity_name,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
