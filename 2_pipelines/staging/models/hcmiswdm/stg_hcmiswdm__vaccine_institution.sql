/*
    HCMIS vaccine module facility dimension.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT institution_id, name, description, sn, rowguid, environment_id
    FROM {{ source('raw', 'hcmiswdm__vaccine_institution') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['institution_id','name','description','sn','rowguid','environment_id']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY institution_id ORDER BY sn DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT institution_id, name, description, sn, rowguid, environment_id,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
