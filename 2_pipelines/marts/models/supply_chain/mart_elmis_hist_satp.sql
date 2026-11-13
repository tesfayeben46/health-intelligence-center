/*
    mart_elmis_hist_satp: Stock According To Plan, adapted. GF's SATP is a
    central-store measure; this is its facility-grain adaptation. Per month by 
    programme, each reporting line is
    banded by its ending stock position:

        stocked_out   stock_in_hand = 0
        understocked  0 < SIH < 1 month of consumption (amc)
        according     amc <= SIH <= max_stock_quantity (the plan ceiling)
        overstocked   SIH > max_stock_quantity

    satp_pct = share of lines "according to plan". Skipped lines excluded
    (DQ rule); lines lacking amc or max_stock_quantity are excluded from
    banding (counted in reporting_lines_unbandable).
*/
{{ config(materialized='table') }}

WITH ctx AS (
    SELECT lnk.hk_elmis_hist_requisition,
           to_char(per.period_start::date, 'YYYY-MM') AS month_id,
           per.period_start::date AS period_start,
           prog.name AS programme_name
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per
        USING (hk_elmis_hist_period)
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_programme_current') }} prog
        USING (hk_elmis_hist_programme)
),
banded AS (
    SELECT
        ctx.month_id, ctx.period_start, ctx.programme_name,
        CASE
            WHEN ls.stock_in_hand::numeric = 0 THEN 'stocked_out'
            WHEN ls.amc IS NULL OR ls.max_stock_quantity IS NULL THEN 'unbandable'
            WHEN ls.stock_in_hand::numeric < ls.amc::numeric THEN 'understocked'
            WHEN ls.stock_in_hand::numeric <= ls.max_stock_quantity::numeric THEN 'according'
            ELSE 'overstocked'
        END AS band
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition_line') }} l
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_line_current') }} ls
        USING (hk_elmis_hist_requisition_line)
    JOIN ctx USING (hk_elmis_hist_requisition)
    WHERE NOT coalesce(ls.skipped::boolean, false)
      AND ls.stock_in_hand IS NOT NULL
)
SELECT
    month_id, period_start, programme_name,
    count(*)                                          AS reporting_lines,
    count(*) FILTER (WHERE band = 'unbandable')       AS reporting_lines_unbandable,
    count(*) FILTER (WHERE band = 'stocked_out')      AS lines_stocked_out,
    count(*) FILTER (WHERE band = 'understocked')     AS lines_understocked,
    count(*) FILTER (WHERE band = 'according')        AS lines_according,
    count(*) FILTER (WHERE band = 'overstocked')      AS lines_overstocked,
    round(100.0 * count(*) FILTER (WHERE band = 'according')
          / NULLIF(count(*) FILTER (WHERE band <> 'unbandable'), 0), 1) AS satp_pct
FROM banded
GROUP BY 1, 2, 3
