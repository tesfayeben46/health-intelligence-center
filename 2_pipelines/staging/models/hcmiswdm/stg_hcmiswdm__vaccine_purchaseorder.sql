/*
    HCMIS vaccine-module procurement pipeline: one row per purchase order.
    Natural key: purchase_order_id. ETA is derived downstream (mart) as
    expected_delivery_date - CURRENT_DATE so it stays live regardless of when
    the source .bak was packed.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT purchase_order_id, po_number, item_id, environment_id,
           supplier_name, quantity, unit_cost,
           order_date, expected_delivery_date, status
    FROM {{ source('raw', 'hcmiswdm__vaccine_purchaseorder') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['purchase_order_id','po_number','item_id','environment_id','supplier_name','quantity','unit_cost','order_date','expected_delivery_date','status']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY purchase_order_id ORDER BY order_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT purchase_order_id, po_number, item_id, environment_id,
       supplier_name, quantity, unit_cost,
       order_date, expected_delivery_date, status,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
