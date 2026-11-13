/*
    mart_elmis_hist_monthly_activity: month by programme national supply
    activity from the legacy eLMIS v2 history.

    Grain: reporting month by programme (the aggregation over all R&R lines
    happens here, once, hence table materialization). Measures:
    requisition volume, dispensed/AMC totals, requested-vs-approved fill
    rate, stockout days. Backs the historical trend widgets.
*/
{{ config(materialized='table') }}

WITH req AS (
    SELECT
        lnk.hk_elmis_hist_requisition,
        prog.name                              AS programme_name,
        to_char(per.period_start::date, 'YYYY-MM') AS month_id,
        per.period_start::date                 AS period_start,
        rs.status,
        rs.emergency::boolean                  AS emergency
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_current') }} rs
        USING (hk_elmis_hist_requisition)
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_programme_current') }} prog
        USING (hk_elmis_hist_programme)
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per
        USING (hk_elmis_hist_period)
),
line_rollup AS (
    SELECT
        l.hk_elmis_hist_requisition,
        count(*)                                        AS line_count,
        sum(ls.quantity_dispensed::numeric)             AS dispensed,
        sum(ls.normalized_consumption::numeric)         AS consumption,
        sum(ls.amc::numeric)                            AS amc,
        sum(ls.quantity_requested::numeric)             AS requested,
        sum(ls.quantity_approved::numeric)              AS approved,
        sum(ls.stockout_days::numeric)                  AS stockout_days
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition_line') }} l
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_line_current') }} ls
        USING (hk_elmis_hist_requisition_line)
    GROUP BY 1
)
SELECT
    req.month_id,
    req.period_start,
    req.programme_name,
    count(*)                                            AS requisitions,
    count(*) FILTER (WHERE req.emergency)               AS emergency_requisitions,
    sum(lr.line_count)                                  AS line_count,
    sum(lr.dispensed)                                   AS dispensed,
    sum(lr.consumption)                                 AS consumption,
    sum(lr.amc)                                         AS amc,
    sum(lr.requested)                                   AS requested,
    sum(lr.approved)                                    AS approved,
    CASE WHEN sum(lr.requested) > 0
         THEN round(100.0 * sum(lr.approved) / sum(lr.requested), 1)
         ELSE NULL END                                  AS fill_rate_pct,
    sum(lr.stockout_days)                               AS stockout_days
FROM req
LEFT JOIN line_rollup lr USING (hk_elmis_hist_requisition)
GROUP BY 1, 2, 3
