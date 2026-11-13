{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        facility_code,
        programme_code,
        processing_schedule_code,
        source_requisition_group_id,
        source_requisition_group_code,
        source_group_member_id,
        source_group_schedule_id,
        direct_delivery,
        drop_off_facility_code
    FROM {{ source('raw', 'elmis_hist__reference_facility_programme_schedule') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['facility_code', 'programme_code', 'processing_schedule_code', 'source_requisition_group_id', 'source_requisition_group_code', 'source_group_member_id', 'source_group_schedule_id', 'direct_delivery', 'drop_off_facility_code']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY facility_code, programme_code, processing_schedule_code ORDER BY source_record_hash) AS rn
    FROM hashed
)

SELECT
    facility_code, programme_code, processing_schedule_code, source_requisition_group_id, source_requisition_group_code, source_group_member_id, source_group_schedule_id, direct_delivery, drop_off_facility_code,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
