/*
    mart_supply_stockout_events: one row per stockout episode by affected region.

    An episode = a contiguous run of zero-stock months for a (hub, commodity),
    detected at the hub grain (where the physical stockout happens:
    HCMIS stock events summed across modes per hub by commodity by month, then
    collapsed with gaps-and-islands). Each hub episode is then attributed to
    every region in the hub's catchment (epss_hub_catchment seed, generated
    from entity-model.yaml; Adama serves Oromia and Central Ethiopia), so a
    region-scoped user sees the hub outages that actually threaten their
    supply. hub_id/hub_name columns carry the physical location. Backs
    rm-events (when / commodity / region /
    days-out / severity).
*/
WITH item AS (
    SELECT h.hk_hcmis_item, sat.full_item_name AS commodity_name
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_item') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_item_current') }} sat
        USING (hk_hcmis_item)
),
env AS (
    SELECT h.hk_hcmis_environment,
           sat.environment_code AS hub_id,
           sat.name             AS hub_name
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_environment') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_environment_current') }} sat
        USING (hk_hcmis_environment)
    WHERE sat.environment_code IS NOT NULL AND sat.environment_code <> ''
),
monthly AS (
    -- Stock on hand summed across the hub's env by mode by day rows per month.
    SELECT
        env.hub_id,
        env.hub_name,
        item.commodity_name,
        TO_CHAR(d_sat.full_date::date, 'YYYY-MM') AS month_id,
        SUM(sat.soh::numeric) AS soh_month
    FROM {{ source('nhdw_vault_raw', 'lnk_hcmis_stock_event') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_stock_event_current') }} sat
        USING (hk_hcmis_stock_event)
    JOIN item USING (hk_hcmis_item)
    JOIN env  USING (hk_hcmis_environment)
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_date_current') }} d_sat
        USING (hk_hcmis_date)
    GROUP BY 1, 2, 3, 4
),
flagged AS (
    SELECT *,
        CASE WHEN soh_month <= 0 THEN 1 ELSE 0 END AS is_out,
        ROW_NUMBER() OVER (PARTITION BY hub_id, commodity_name ORDER BY month_id) AS rn,
        ROW_NUMBER() OVER (PARTITION BY hub_id, commodity_name,
                           CASE WHEN soh_month <= 0 THEN 1 ELSE 0 END ORDER BY month_id) AS rn_out
    FROM monthly
),
episodes AS (
    SELECT
        hub_id, hub_name, commodity_name,
        MIN(month_id) AS episode_start_month,
        MAX(month_id) AS episode_end_month,
        COUNT(*)      AS months_out
    FROM flagged
    WHERE is_out = 1
    GROUP BY hub_id, hub_name, commodity_name, (rn - rn_out)
),
latest AS (SELECT MAX(month_id) AS m FROM monthly)
SELECT
    ROW_NUMBER() OVER (ORDER BY c.region_id, e.commodity_name, e.episode_start_month, e.hub_id) AS episode_id,
    c.region_id,
    e.hub_id,
    e.hub_name,
    e.commodity_name,
    cr.programme_id,
    e.episode_start_month,
    e.episode_end_month,
    e.months_out,
    e.months_out * 30 AS days_out,
    CASE WHEN e.months_out >= 3 THEN 'critical'
         WHEN e.months_out = 2  THEN 'watch'
         ELSE 'alert' END AS severity,
    e.episode_end_month AS last_flagged_month,
    CASE WHEN e.episode_end_month = latest.m THEN 'active' ELSE 'resolved' END AS status
FROM episodes e
JOIN {{ ref('epss_hub_catchment') }} c ON c.hub_id = e.hub_id
LEFT JOIN {{ ref('commodity_reference') }} cr ON cr.commodity_name = e.commodity_name
CROSS JOIN latest
