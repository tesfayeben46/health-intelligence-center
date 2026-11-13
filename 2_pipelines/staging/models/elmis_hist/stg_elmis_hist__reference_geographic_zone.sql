{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        code,
        name,
        geographic_level_code,
        parent_zone_code,
        catchment_population,
        latitude,
        longitude
    FROM {{ source('raw', 'elmis_hist__reference_geographic_zone') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['code', 'name', 'geographic_level_code', 'parent_zone_code', 'catchment_population', 'latitude', 'longitude']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY code ORDER BY source_record_hash) AS rn
    FROM hashed
)

SELECT
    code, name, geographic_level_code, parent_zone_code, catchment_population, latitude, longitude,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
