/*
    mart_elmis_hist_availability: "period availability" (an OSA proxy) per
    month by product set by product, from the legacy eLMIS
    v2 history.

    Not true OSA: GF's On-Shelf Availability is a physical visit-day check.
    This is the LMIS equivalent: the share of reporting (non-skipped)
    facility-lines for the product that ended the period in stock
    (stock_in_hand > 0) with zero stockout days. Labeled "period availability"
    everywhere downstream; never "OSA".

    product_set: 'tracer' = the tracer-flagged products;
                 'ppe'    = name-matched PPE items.
    DQ rule: skipped lines are excluded from all denominators.
*/
{{ config(materialized='table') }}

WITH product_sets AS (
    SELECT hk_elmis_hist_product, primary_name, 'tracer' AS product_set
    FROM {{ source('nhdw_vault_business', 'v_sat_elmis_hist_product_current') }}
    WHERE tracer::boolean
    UNION ALL
    SELECT hk_elmis_hist_product, primary_name, 'ppe'
    FROM {{ source('nhdw_vault_business', 'v_sat_elmis_hist_product_current') }}
    WHERE primary_name ~* 'mask|glove|gown|respirator|sanitiz|apron|face shield|goggle'
),
ctx AS (
    SELECT lnk.hk_elmis_hist_requisition,
           to_char(per.period_start::date, 'YYYY-MM') AS month_id,
           per.period_start::date AS period_start
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per
        USING (hk_elmis_hist_period)
)
SELECT
    ctx.month_id,
    ctx.period_start,
    ps.product_set,
    ps.primary_name                          AS product_name,
    count(*)                                 AS reporting_lines,
    count(*) FILTER (WHERE ls.stock_in_hand::numeric > 0
                       AND coalesce(ls.stockout_days::numeric, 0) = 0)
                                             AS available_lines,
    round(100.0 * count(*) FILTER (WHERE ls.stock_in_hand::numeric > 0
                       AND coalesce(ls.stockout_days::numeric, 0) = 0)
          / NULLIF(count(*), 0), 1)          AS availability_pct
FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition_line') }} l
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_line_current') }} ls
    USING (hk_elmis_hist_requisition_line)
JOIN product_sets ps USING (hk_elmis_hist_product)
JOIN ctx USING (hk_elmis_hist_requisition)
WHERE NOT coalesce(ls.skipped::boolean, false)
  AND ls.stock_in_hand IS NOT NULL
GROUP BY 1, 2, 3, 4
