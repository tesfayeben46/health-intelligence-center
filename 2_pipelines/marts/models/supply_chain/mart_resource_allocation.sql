/*
    mart_resource_allocation: month by commodity grain, national.

    The HCMIS stock-event link carries no facility FK (grain is
    item by environment by mode by date), so this mart aggregates across
    all stocking environments; there is no per-facility or
    per-woreda cut here. The gateway fetchers (rm-hero/rm-statboard/
    rm-forecast/rm-order-next/rm-wastage) consume it as national
    (geo_col=None).

    Aggregates HCMIS Fact.DailyRecord (consumption + AMC) into a
    monthly view used by:
      - BI Resource hero card / status board
      - BI Resource drug-stock heatmap

    Metrics:
      - amc                 average monthly consumption (12-month moving)
      - soh_eom_avg         stock on hand, averaged across env/mode/day rows
      - mos                 months of stock = soh_eom_avg / amc
      - stockout_days       days in the month with ≥1 zero-SOH stock event
                            anywhere in the country (bounded 0..31)
      - wastage_units       expired + damaged

    Satellite reads go through the current-record views (sats are
    insert-only; raw-sat joins double-count once history accumulates).
*/
WITH item AS (
    SELECT
        h.hk_hcmis_item,
        sat.full_item_name AS commodity_name
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_item') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_item_current') }} sat
        USING (hk_hcmis_item)
),
month_facts AS (
    SELECT
        item.commodity_name,
        d_sat.full_date::date       AS full_date,
        TO_CHAR(d_sat.full_date::date, 'YYYY-MM') AS month_id,
        sat.amc12::numeric          AS amc12,
        sat.soh::numeric            AS soh,
        sat.amount_received::numeric AS amount_received,
        sat.amount_expired::numeric AS amount_expired,
        sat.amount_damaged::numeric AS amount_damaged
    FROM {{ source('nhdw_vault_raw', 'lnk_hcmis_stock_event') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_stock_event_current') }} sat
        USING (hk_hcmis_stock_event)
    JOIN item USING (hk_hcmis_item)
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_date_current') }} d_sat
        USING (hk_hcmis_date)
)
SELECT
    mf.month_id,
    mf.commodity_name,
    cr.programme_id,
    AVG(mf.amc12) AS amc,
    AVG(mf.soh)   AS soh_eom_avg,
    SUM(mf.amount_received) AS received,
    SUM(mf.amount_expired + mf.amount_damaged) AS wastage_units,
    COUNT(DISTINCT mf.full_date) FILTER (WHERE mf.soh = 0) AS stockout_days,
    CASE WHEN AVG(mf.amc12) > 0
         THEN ROUND(AVG(mf.soh)::numeric / AVG(mf.amc12)::numeric, 2)
         ELSE NULL END AS mos
FROM month_facts mf
LEFT JOIN {{ ref('commodity_reference') }} cr ON cr.commodity_name = mf.commodity_name
GROUP BY 1,2,3
