/*
    Climate weekly meteorological drivers: typed, deduped staging.
    Source: nhdw_staging.raw_climate__weekly (s3() view over Parquet).
    Grain: region by ISO-week.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT iso_week, week_start, region_id, rainfall_mm, temp_celsius, humidity_pct
    FROM {{ source('raw', 'climate__weekly') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['iso_week','week_start','region_id','rainfall_mm','temp_celsius','humidity_pct']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *,
           row_number() OVER (
               PARTITION BY region_id, iso_week
               ORDER BY week_start DESC NULLS LAST, source_record_hash
           ) AS rn
    FROM hashed
)
SELECT
    iso_week, week_start, region_id, rainfall_mm, temp_celsius, humidity_pct,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
