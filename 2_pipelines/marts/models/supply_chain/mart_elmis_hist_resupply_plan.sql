/*
    mart_elmis_hist_resupply_plan: the planning-module mock mart. For every
    facility by product in the latest reported month of the archive, the stock
    position and a suggested resupply
    quantity using the source's own max-stock logic (SATP banding, adapted):
        months_of_stock   = stock_in_hand / amc
        suggested_order   = greatest(max_stock_quantity - stock_in_hand, 0)
        band              = stocked_out | understocked | according_to_plan | overstocked
    It is a deterministic plan derived from actuals; the forecasting package refines the
    demand term; an optimisation step (dispatch/route) would consume this mart.
    Tracer-only by default to keep the plan actionable; the
    full table is one filter away in mart_elmis_hist_facility_product_month.
*/
{{ config(materialized='table') }}

WITH latest AS (
    SELECT facility_code, product_code, max(month_id) AS month_id
    FROM {{ ref('mart_elmis_hist_facility_product_month') }}
    GROUP BY 1, 2
)
SELECT
    m.facility_code, m.facility_type_code, m.geographic_zone_code,
    m.product_code, m.product_name, m.programme_name, m.month_id AS as_of_month,
    m.stock_in_hand, m.amc, m.max_stock_quantity, m.quantity_dispensed AS last_month_dispensed,
    CASE WHEN m.amc > 0 THEN round(m.stock_in_hand / m.amc, 2) END        AS months_of_stock,
    greatest(COALESCE(m.max_stock_quantity, 0) - COALESCE(m.stock_in_hand, 0), 0) AS suggested_order_qty,
    CASE
        WHEN COALESCE(m.stock_in_hand, 0) <= 0                                   THEN 'stocked_out'
        WHEN m.amc > 0 AND m.stock_in_hand / m.amc < 1                            THEN 'understocked'
        WHEN m.max_stock_quantity > 0 AND m.stock_in_hand > m.max_stock_quantity  THEN 'overstocked'
        ELSE 'according_to_plan'
    END AS plan_band,
    m.stockout_days AS last_month_stockout_days
FROM {{ ref('mart_elmis_hist_facility_product_month') }} m
JOIN latest USING (facility_code, product_code, month_id)
WHERE COALESCE(m.tracer::text, 'false') IN ('true', 't', '1')
