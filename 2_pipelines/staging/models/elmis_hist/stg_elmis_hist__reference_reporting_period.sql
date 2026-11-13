{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        source_id,
        processing_schedule_code,
        name,
        period_start,
        period_end,
        number_of_months
    FROM {{ source('raw', 'elmis_hist__reference_reporting_period') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['source_id', 'processing_schedule_code', 'name', 'period_start', 'period_end', 'number_of_months']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY source_id ORDER BY source_record_hash) AS rn
    FROM hashed
)

SELECT
    source_id, processing_schedule_code, name, period_start, period_end, number_of_months,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
