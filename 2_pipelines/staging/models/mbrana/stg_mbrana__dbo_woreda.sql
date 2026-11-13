/*
    mBrana woreda dimension (level 4 of the admin tree).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT id, woreda_name, zone_id, zone_guid, woreda_code, p_code,
           latitude, longitude, is_active, created_date, modified_date, rowguid
    FROM {{ source('raw', 'mbrana__dbo_woreda') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','woreda_name','zone_id','zone_guid','woreda_code','p_code','latitude','longitude','is_active','created_date','modified_date','rowguid']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY modified_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT id, woreda_name, zone_id, zone_guid, woreda_code, p_code,
       latitude, longitude, is_active, created_date, modified_date, rowguid,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
