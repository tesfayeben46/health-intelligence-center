/*
    DHIS2 data sets: collections of data elements reported at a period type.
    Dedupes on `id`.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT id, code, name, period_type, category_combo__id AS category_combo_id, last_updated
    FROM {{ source('raw', 'dhis2__data_sets') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','code','name','period_type','category_combo_id','last_updated']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY last_updated DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT
    id, code, name, period_type, category_combo_id, last_updated,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
