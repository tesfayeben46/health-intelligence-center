/*
    mBrana institution master (HCMISWCloud canonical facility table).
    Dedupes on `id` keeping the latest `modified_date`.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT id, name, description, phone, woreda, town, kebele, ru_type, ownership, zone,
           administrative_unit_id, operational_status, latitude, longitude,
           active, in_process, created_date, modified_date, alternate_name,
           reporting_group_id, environment_id, rowguid
    FROM {{ source('raw', 'mbrana__dbo_institution') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','name','description','phone','woreda','town','kebele','ru_type','ownership','zone','administrative_unit_id','operational_status','latitude','longitude','active','in_process','created_date','modified_date','alternate_name','reporting_group_id','environment_id','rowguid']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY modified_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT id, name, description, phone, woreda, town, kebele, ru_type, ownership, zone,
       administrative_unit_id, operational_status, latitude, longitude,
       active, in_process, created_date, modified_date, alternate_name,
       reporting_group_id, environment_id, rowguid,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
