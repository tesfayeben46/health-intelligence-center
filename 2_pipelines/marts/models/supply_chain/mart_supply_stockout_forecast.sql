/*
    mart_supply_stockout_forecast: environment by commodity, point-in-time.

    For each (environment, commodity) projects the days-to-stockout based
    on the 12-month AMC and the latest on-hand quantity.
    Used by BI Resource (days-to-stockout timeline + recent stockouts).

    Satellite reads go through the current-record views (sats are
    insert-only); the ROW_NUMBER additionally tiebreaks on load_ts DESC
    then the stock-event hash key so the "latest" pick is deterministic
    when several rows share the newest date (multiple modes report the
    same day).
*/
WITH latest AS (
    SELECT
        sat.hk_hcmis_stock_event,
        d_sat.full_date::date AS as_of,
        sat.soh::numeric   AS soh,
        sat.amc12::numeric AS amc12,
        ROW_NUMBER() OVER (
            PARTITION BY lnk.hk_hcmis_item, lnk.hk_hcmis_environment
            ORDER BY d_sat.full_date::date DESC,
                     sat.load_ts DESC,
                     sat.hk_hcmis_stock_event) AS rn,
        lnk.hk_hcmis_item,
        lnk.hk_hcmis_environment
    FROM {{ source('nhdw_vault_raw', 'lnk_hcmis_stock_event') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_stock_event_current') }} sat
        USING (hk_hcmis_stock_event)
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_date_current') }} d_sat
        USING (hk_hcmis_date)
),
item AS (
    SELECT h.hk_hcmis_item, sat.full_item_name AS commodity_name
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_item') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_item_current') }} sat
        USING (hk_hcmis_item)
)
SELECT
    item.commodity_name,
    latest.hk_hcmis_environment,                 -- bridge to facility via env->facility map
    latest.as_of,
    latest.soh AS soh_now,
    latest.amc12 AS amc,
    CASE WHEN latest.amc12 > 0
         THEN ROUND( (latest.soh::numeric / latest.amc12::numeric) * 30 )
         ELSE NULL END AS projected_days_remaining,
    CASE WHEN latest.amc12 > 0
         THEN (latest.as_of + (latest.soh::numeric / latest.amc12::numeric * 30)::int)
         ELSE NULL END AS projected_stockout_date,
    CASE
        WHEN latest.soh = 0                        THEN 'stockout'
        WHEN latest.soh < latest.amc12             THEN 'risk'
        WHEN latest.soh < latest.amc12 * 2         THEN 'watch'
        ELSE 'ok'
    END AS status
FROM latest
JOIN item USING (hk_hcmis_item)
WHERE rn = 1
