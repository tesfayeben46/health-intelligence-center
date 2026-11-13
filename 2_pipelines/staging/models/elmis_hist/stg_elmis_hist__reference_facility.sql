{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        code,
        name,
        facility_type_code,
        geographic_zone_code,
        parent_facility_code,
        catchment_population,
        latitude,
        longitude,
        service_delivery_point,
        active,
        enabled,
        virtual_facility,
        go_live_date,
        go_down_date
    FROM {{ source('raw', 'elmis_hist__reference_facility') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['code', 'name', 'facility_type_code', 'geographic_zone_code', 'parent_facility_code', 'catchment_population', 'latitude', 'longitude']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY code ORDER BY source_record_hash) AS rn
    FROM hashed
)

SELECT
    code, name, facility_type_code, geographic_zone_code, parent_facility_code, catchment_population, latitude, longitude, service_delivery_point, active, enabled, virtual_facility, go_live_date, go_down_date,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
