/*
    mart_elmis_hist_product_stockout: month by tracer product stockout
    pressure from the legacy eLMIS v2 history.

    Grain: reporting month by tracer product (tracer = the nationally
    monitored commodity shortlist, so the table stays small and the
    products are the ones a decision-maker recognises). Measures: total
    stockout days, share of reporting facility-lines out of stock, dispensed
    volume. Table materialization: one aggregation over all R&R lines per pipeline
    run, not per dashboard query.
*/
{{ config(materialized='table') }}

WITH ctx AS (
    SELECT
        lnk.hk_elmis_hist_requisition,
        to_char(per.period_start::date, 'YYYY-MM') AS month_id,
        per.period_start::date                     AS period_start
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per
        USING (hk_elmis_hist_period)
),
tracer AS (
    SELECT hk_elmis_hist_product, primary_name, product_group_code
    FROM {{ source('nhdw_vault_business', 'v_sat_elmis_hist_product_current') }}
    WHERE tracer::boolean
)
SELECT
    ctx.month_id,
    ctx.period_start,
    tracer.primary_name                                  AS product_name,
    tracer.product_group_code,
    count(*)                                             AS reporting_lines,
    count(*) FILTER (WHERE ls.stockout_days::numeric > 0) AS lines_with_stockout,
    round(100.0 * count(*) FILTER (WHERE ls.stockout_days::numeric > 0)
          / NULLIF(count(*), 0), 1)                      AS stockout_line_pct,
    sum(ls.stockout_days::numeric)                       AS stockout_days,
    sum(ls.quantity_dispensed::numeric)                  AS dispensed,
    sum(ls.amc::numeric)                                 AS amc
FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition_line') }} l
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_line_current') }} ls
    USING (hk_elmis_hist_requisition_line)
JOIN tracer USING (hk_elmis_hist_product)
JOIN ctx   USING (hk_elmis_hist_requisition)
GROUP BY 1, 2, 3, 4
