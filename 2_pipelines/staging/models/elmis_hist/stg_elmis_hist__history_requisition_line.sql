{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        source_line_item_id,
        source_requisition_id,
        product_code,
        beginning_balance,
        quantity_received,
        quantity_dispensed,
        stock_in_hand,
        quantity_requested,
        calculated_order_quantity,
        quantity_approved,
        total_losses_and_adjustments,
        stockout_days,
        normalized_consumption,
        amc,
        max_stock_quantity,
        packs_to_ship,
        skipped,
        full_supply,
        reporting_days,
        line_item_created_at,
        line_item_modified_at                    AS modified_date
    FROM {{ source('raw', 'elmis_hist__history_requisition_line') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['source_line_item_id', 'source_requisition_id', 'product_code', 'beginning_balance', 'quantity_received', 'quantity_dispensed', 'stock_in_hand', 'quantity_requested']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY source_line_item_id ORDER BY modified_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)

SELECT
    source_line_item_id, source_requisition_id, product_code, beginning_balance, quantity_received, quantity_dispensed, stock_in_hand, quantity_requested, calculated_order_quantity, quantity_approved, total_losses_and_adjustments, stockout_days, normalized_consumption, amc, max_stock_quantity, packs_to_ship, skipped, full_supply, reporting_days, line_item_created_at, modified_date,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
