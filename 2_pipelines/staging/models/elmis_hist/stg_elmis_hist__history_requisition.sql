{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        source_requisition_id,
        facility_code,
        programme_code,
        source_period_id,
        status,
        emergency,
        source_application,
        requisition_created_at,
        requisition_modified_at                  AS modified_date
    FROM {{ source('raw', 'elmis_hist__history_requisition') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['source_requisition_id', 'facility_code', 'programme_code', 'source_period_id', 'status', 'emergency', 'source_application', 'requisition_created_at']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY source_requisition_id ORDER BY modified_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)

SELECT
    source_requisition_id, facility_code, programme_code, source_period_id, status, emergency, source_application, requisition_created_at, modified_date,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
