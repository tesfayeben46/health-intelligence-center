/*
    DHIS2 data elements: the metric definitions referenced by data_value_sets.
    Dedupes on `id` (DHIS2 UID) keeping the latest `last_updated`.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT id, code, name, short_name, value_type, aggregation_type, domain_type,
           category_combo__id AS category_combo_id, last_updated
    FROM {{ source('raw', 'dhis2__data_elements') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','code','name','short_name','value_type','aggregation_type','domain_type','category_combo_id','last_updated']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY last_updated DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT
    id, code, name, short_name, value_type, aggregation_type, domain_type,
    category_combo_id, last_updated,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
