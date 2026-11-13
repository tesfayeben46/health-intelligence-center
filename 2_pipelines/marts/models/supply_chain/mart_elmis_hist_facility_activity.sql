/*
    mart_elmis_hist_facility_activity: facility by programme by month rollup
    of the legacy eLMIS v2 history (bounded by the requisition count).
    Carries the facility's zone chain for geographic grouping.

    Governance: facility names/codes are classified for internal dashboards
    only.
*/
{{ config(materialized='table') }}

WITH fac AS (
    SELECT h.hk_elmis_hist_facility,
           h.code AS facility_code,
           fs.name AS facility_name,
           fs.facility_type_code,
           fs.geographic_zone_code,
           z.name  AS zone_name,
           z.parent_zone_code
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_facility') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_facility_current') }} fs
        USING (hk_elmis_hist_facility)
    LEFT JOIN {{ source('nhdw_vault_raw', 'hub_elmis_hist_zone') }} zh
        ON zh.code = fs.geographic_zone_code
    LEFT JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_zone_current') }} z
        ON z.hk_elmis_hist_zone = zh.hk_elmis_hist_zone
),
line_rollup AS (
    SELECT
        l.hk_elmis_hist_requisition,
        count(*)                             AS line_count,
        sum(ls.quantity_requested::numeric)  AS requested,
        sum(ls.quantity_approved::numeric)   AS approved,
        sum(ls.stockout_days::numeric)       AS stockout_days
    FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition_line') }} l
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_line_current') }} ls
        USING (hk_elmis_hist_requisition_line)
    GROUP BY 1
)
SELECT
    fac.facility_code,
    fac.facility_name,
    fac.facility_type_code,
    fac.zone_name,
    prog.name                                  AS programme_name,
    to_char(per.period_start::date, 'YYYY-MM') AS month_id,
    per.period_start::date                     AS period_start,
    count(*)                                   AS requisitions,
    sum(lr.line_count)                         AS line_count,
    sum(lr.requested)                          AS requested,
    sum(lr.approved)                           AS approved,
    CASE WHEN sum(lr.requested) > 0
         THEN round(100.0 * sum(lr.approved) / sum(lr.requested), 1)
         ELSE NULL END                         AS fill_rate_pct,
    sum(lr.stockout_days)                      AS stockout_days
FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
JOIN fac  USING (hk_elmis_hist_facility)
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_programme_current') }} prog
    USING (hk_elmis_hist_programme)
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per
    USING (hk_elmis_hist_period)
LEFT JOIN line_rollup lr USING (hk_elmis_hist_requisition)
GROUP BY 1, 2, 3, 4, 5, 6, 7
