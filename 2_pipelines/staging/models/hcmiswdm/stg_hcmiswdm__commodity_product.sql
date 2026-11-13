/*
    HCMIS commodity master: the product catalogue. Includes vaccine + non-vaccine.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT product_id, name, description, commodity_type_id, is_active, rowguid, sn,
           is_vaccine, is_nps, prescription_type
    FROM {{ source('raw', 'hcmiswdm__commodity_product') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['product_id','name','description','commodity_type_id','is_active','rowguid','sn','is_vaccine','is_nps','prescription_type']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY product_id ORDER BY sn DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT product_id, name, description, commodity_type_id, is_active, rowguid, sn,
       is_vaccine, is_nps, prescription_type,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
