/*
    mart_supply_region_stock: month by commodity by region months-on-hand.

    The HCMIS stock-event grain is item by environment by mode by date; each
    environment is an EPSS hub. Hubs are warehouses, not regions: real EPSS
    hubs supply multi-region catchments (Central Ethiopia and South West
    Ethiopia have no hub of their own; they are served from Adama/Shashemene/
    Hawassa and Jimma/Sodo). Attributing stock to the hub's home region would
    leave CE and SW absent from every regional supply signal.

    Regional attribution rides the epss_hub_catchment seed (generated from
    entity-model.yaml `serves` lists, the single source of truth): a hub's stock
    and consumption are allocated to its served regions by facility-share
    weight (Σ weights = 1 per hub, so nothing is double-counted), then
    mos = Σ(w·soh) / Σ(w·amc) per region. A region served by several hubs
    blends them; all 14 regions are covered.

    Backs rm-stock-heatmap and the readiness leg of dz-region-map.
    Complements the national mart_resource_allocation.
*/
WITH item AS (
    SELECT h.hk_hcmis_item, sat.full_item_name AS commodity_name
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_item') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_item_current') }} sat
        USING (hk_hcmis_item)
),
env AS (
    -- environment_code = the canonical EPSS hub id (CENT/ADAMA/…)
    SELECT h.hk_hcmis_environment, sat.environment_code AS hub_id
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_environment') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_environment_current') }} sat
        USING (hk_hcmis_environment)
    WHERE sat.environment_code IS NOT NULL AND sat.environment_code <> ''
),
facts AS (
    SELECT
        item.commodity_name,
        env.hub_id,
        TO_CHAR(d_sat.full_date::date, 'YYYY-MM') AS month_id,
        sat.amc12::numeric AS amc12,
        sat.soh::numeric   AS soh
    FROM {{ source('nhdw_vault_raw', 'lnk_hcmis_stock_event') }} lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_stock_event_current') }} sat
        USING (hk_hcmis_stock_event)
    JOIN item USING (hk_hcmis_item)
    JOIN env  USING (hk_hcmis_environment)
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_date_current') }} d_sat
        USING (hk_hcmis_date)
),
hub_month AS (
    -- One row per hub by commodity by month (averaged across modes/days first,
    -- so a hub's weight applies once, not once per mode row).
    SELECT hub_id, commodity_name, month_id,
           AVG(soh)   AS soh_hub,
           AVG(amc12) AS amc_hub
    FROM facts
    GROUP BY 1, 2, 3
)
SELECT
    hm.month_id,
    hm.commodity_name,
    cr.programme_id,
    c.region_id,
    ROUND(SUM(c.weight::numeric * hm.soh_hub)::numeric, 1) AS soh_eom_avg,
    ROUND(SUM(c.weight::numeric * hm.amc_hub)::numeric, 1) AS amc,
    CASE WHEN SUM(c.weight::numeric * hm.amc_hub) > 0
         THEN ROUND((SUM(c.weight::numeric * hm.soh_hub)
                     / SUM(c.weight::numeric * hm.amc_hub))::numeric, 2)
         ELSE NULL END AS mos
FROM hub_month hm
JOIN {{ ref('epss_hub_catchment') }} c ON c.hub_id = hm.hub_id
LEFT JOIN {{ ref('commodity_reference') }} cr ON cr.commodity_name = hm.commodity_name
GROUP BY 1, 2, 3, 4
