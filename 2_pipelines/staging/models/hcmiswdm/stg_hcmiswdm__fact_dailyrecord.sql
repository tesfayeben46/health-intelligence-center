/*
    HCMIS daily stock fact: the canonical stock-on-hand fact.
    Natural key: (item_id, environment_id, mode_id, date_id). No `last_updated`
    in source; assume idempotent reloads (dedupe keeps the highest
    soh_amount as a synthetic "latest extract" order, with the full-row
    fingerprint as the deterministic final tie-breaker).
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        item_id, environment_id, mode_id, date_id,
        mos12, amc12,
        soh, soh_amount,
        standard_issued, amount_standard_issued, revenue_standard_issued,
        facility_issued, amount_facility_issued, revenue_facility_issued,
        center_hub_issued, amount_center_hub_issued, revenue_center_hub_issued,
        expired, amount_expired,
        damaged, amount_damaged,
        received, amount_received,
        bb_received, amount_bb_received,
        standard_quantity_requested, stock_status, fiscal_year
    FROM {{ source('raw', 'hcmiswdm__fact_dailyrecord') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['item_id','environment_id','mode_id','date_id','mos12','amc12','soh','soh_amount','standard_issued','amount_standard_issued','revenue_standard_issued','facility_issued','amount_facility_issued','revenue_facility_issued','center_hub_issued','amount_center_hub_issued','revenue_center_hub_issued','expired','amount_expired','damaged','amount_damaged','received','amount_received','bb_received','amount_bb_received','standard_quantity_requested','stock_status','fiscal_year']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (
        PARTITION BY item_id, environment_id, mode_id, date_id
        ORDER BY soh_amount DESC NULLS LAST, source_record_hash
    ) AS rn
    FROM hashed
)
SELECT
    item_id, environment_id, mode_id, date_id,
    mos12, amc12, soh, soh_amount,
    standard_issued, amount_standard_issued, revenue_standard_issued,
    facility_issued, amount_facility_issued, revenue_facility_issued,
    center_hub_issued, amount_center_hub_issued, revenue_center_hub_issued,
    expired, amount_expired,
    damaged, amount_damaged,
    received, amount_received,
    bb_received, amount_bb_received,
    standard_quantity_requested, stock_status, fiscal_year,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
