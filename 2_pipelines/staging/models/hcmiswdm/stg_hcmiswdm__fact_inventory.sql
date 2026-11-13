/*
    HCMIS inventory fact (lighter than daily_record; aggregates by environment/mode/date only).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT environment_id, mode_id, date_id, amount_issued, soh_amount, amount_received, fiscal_year
    FROM {{ source('raw', 'hcmiswdm__fact_inventory') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['environment_id','mode_id','date_id','amount_issued','soh_amount','amount_received','fiscal_year']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY environment_id, mode_id, date_id ORDER BY soh_amount DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT environment_id, mode_id, date_id, amount_issued, soh_amount, amount_received, fiscal_year,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
