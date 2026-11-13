/*
    mart_elmis_hist_facility_product_month: facility by product by month grain of the
    legacy eLMIS v2 history: the feature mart for `elmis_consumption_forecast` and the
    base of the planning mock (mart_elmis_hist_resupply_plan). One row per reported
    (non-skipped) R&R line rolled to the calendar month of the period start.

    Skipped lines are excluded: they mean "did not report this product", never
    zero consumption. Quantities carry the pattern-preserving mutation of the
    source extract: relative patterns hold, absolute units do not.

    Governance: facility codes are restricted to internal dashboards. The
    archive never joins the entity model.
*/
{{ config(materialized='table') }}

WITH fac AS (
    SELECT h.hk_elmis_hist_facility, h.code AS facility_code, fs.facility_type_code, fs.geographic_zone_code
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_facility') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_facility_current') }} fs USING (hk_elmis_hist_facility)
),
prod AS (
    SELECT h.hk_elmis_hist_product, h.code AS product_code, ps.primary_name AS product_name,
           ps.tracer, ps.product_group_code, ps.dispensing_unit
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_product') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_product_current') }} ps USING (hk_elmis_hist_product)
),
req AS (
    SELECT lnk.hk_elmis_hist_requisition, lnk.hk_elmis_hist_facility, lnk.hk_elmis_hist_programme,
           per.period_start::date AS period_start, per.processing_schedule_code,
           to_char(per.period_start::date, 'YYYY-MM') AS month_id
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per USING (hk_elmis_hist_period)
)
SELECT
    fac.facility_code,
    fac.facility_type_code,
    fac.geographic_zone_code,
    prod.product_code,
    prod.product_name,
    prod.tracer,
    prod.product_group_code,
    prog.name                                        AS programme_name,
    req.month_id,
    min(req.period_start)                            AS period_start,
    max(req.processing_schedule_code)                AS processing_schedule_code,
    count(*)                                         AS lines,
    sum(ls.beginning_balance::numeric)               AS beginning_balance,
    sum(ls.quantity_received::numeric)               AS quantity_received,
    sum(ls.quantity_dispensed::numeric)              AS quantity_dispensed,
    sum(ls.normalized_consumption::numeric)          AS normalized_consumption,
    sum(ls.stock_in_hand::numeric)                   AS stock_in_hand,
    avg(ls.amc::numeric)                             AS amc,
    max(ls.max_stock_quantity::numeric)              AS max_stock_quantity,
    sum(ls.calculated_order_quantity::numeric)       AS calculated_order_quantity,
    sum(ls.quantity_approved::numeric)               AS quantity_approved,
    sum(ls.stockout_days::numeric)                   AS stockout_days,
    max(ls.reporting_days::numeric)                  AS reporting_days
FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition_line') }} l
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_line_current') }} ls USING (hk_elmis_hist_requisition_line)
JOIN req  USING (hk_elmis_hist_requisition)
JOIN fac  USING (hk_elmis_hist_facility)
JOIN prod USING (hk_elmis_hist_product)
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_programme_current') }} prog USING (hk_elmis_hist_programme)
WHERE COALESCE(ls.skipped::text, 'false') NOT IN ('true', 't', '1')
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
