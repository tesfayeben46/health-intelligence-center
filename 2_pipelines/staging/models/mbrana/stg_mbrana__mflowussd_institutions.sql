/*
    mBrana mFlowUSSD facility dimension (lightweight tree).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT institution_id, institution_name, parent_institution_id
    FROM {{ source('raw', 'mbrana__mflowussd_institutions') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['institution_id','institution_name','parent_institution_id']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY institution_id ORDER BY institution_name DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT institution_id, institution_name, parent_institution_id,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
