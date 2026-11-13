/*
    mBrana zone dimension (level 3 of the admin tree, between region & woreda).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT id, zone_name, region_id, region_guid, zone_code, p_code,
           latitude, longitude, is_active, created_date, modified_date, rowguid
    FROM {{ source('raw', 'mbrana__dbo_zone') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','zone_name','region_id','region_guid','zone_code','p_code','latitude','longitude','is_active','created_date','modified_date','rowguid']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY modified_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT id, zone_name, region_id, region_guid, zone_code, p_code,
       latitude, longitude, is_active, created_date, modified_date, rowguid,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
