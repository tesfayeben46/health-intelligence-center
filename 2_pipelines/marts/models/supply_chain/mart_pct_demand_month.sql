{{ config(materialized='table') }}
{#
    PCT demand base: one row per facility by product by programme by month on the
    Zambia eLMIS archive (bi_supply.mart_pct_demand_month). The single place the
    PCT marts (KPIs, position, forecast surfaces) take their monthly facility view
    from.

    Grain: the source feature mart is already one row per facility by product by 
    programme by month (no key has a second report). This model keys on
    month_id, never on the source's period_start, which for some programmes starts
    mid-month and would split one calendar month into several groups. The
    aggregations are defensive: if a key ever carried a second report in a month,
    stock state would come from the latest (period end), stockout = any report,
    quantities summed and line-capped (approved at the calculated quantity;
    stockout days at reporting days).

    adjusted_demand is eLMIS's own normalized_consumption (the system of record's
    stockout-adjusted consumption, from which its AMC is built), not a re-derivation.
    Where it is NULL the dispensed quantity stands in; demand_basis says which.
    demand_censored flags months with any stockout: demand observed there is a
    floor, not a measurement.

    pipeline_assumed is a planning assumption, not a measurement: the month's
    approved quantity is taken to be in transit. Receipts are too sparsely
    recorded in this archive to measure the pipeline.
#}

WITH province AS (
    SELECT d.code          AS district_code,
           ds.name         AS district_name,
           p.code          AS region_id,
           ps.name         AS region_name
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_zone') }} d
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_zone_current') }} ds USING (hk_elmis_hist_zone)
    JOIN {{ source('nhdw_vault_raw', 'hub_elmis_hist_zone') }} p ON p.code = ds.parent_zone_code
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_zone_current') }} ps ON ps.hk_elmis_hist_zone = p.hk_elmis_hist_zone
    WHERE ds.geographic_level_code = 'District' AND ps.geographic_level_code = 'Province'
),

programme AS (
    SELECT h.code AS programme_id, s.name AS programme_name
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_programme') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_programme_current') }} s USING (hk_elmis_hist_programme)
),

fpm AS (
    SELECT f.*, pv.district_code, pv.district_name, pv.region_id, pv.region_name, pg.programme_id
    FROM {{ ref('mart_elmis_hist_facility_product_month') }} f
    JOIN province  pv ON pv.district_code = f.geographic_zone_code
    JOIN programme pg ON pg.programme_name = f.programme_name
)

SELECT facility_code, facility_type_code, product_code, product_name, tracer,
       district_code, district_name, region_id, region_name, programme_id, programme_name,
       month_id, (month_id || '-01')::date                                   AS period_start,
       (array_agg(stock_in_hand ORDER BY period_start DESC))[1]              AS stock_in_hand,
       (array_agg(amc           ORDER BY period_start DESC))[1]              AS amc,
       sum(quantity_dispensed)                                               AS dispensed,
       sum(normalized_consumption)                                           AS normalized_consumption,
       CASE WHEN bool_and(normalized_consumption IS NOT NULL)
            THEN sum(normalized_consumption) ELSE sum(quantity_dispensed) END AS adjusted_demand,
       CASE WHEN bool_and(normalized_consumption IS NOT NULL)
            THEN 'normalized_consumption' ELSE 'dispensed_fallback' END      AS demand_basis,
       bool_or(stockout_days > 0)                                            AS stockout_any,
       bool_or(stockout_days > 0)                                            AS demand_censored,
       sum(least(coalesce(stockout_days, 0), reporting_days)) FILTER (WHERE reporting_days > 0) AS stockout_days,
       sum(reporting_days) FILTER (WHERE reporting_days > 0)                 AS reporting_days,
       sum(quantity_approved)                                                AS quantity_approved,
       sum(least(coalesce(quantity_approved, 0), calculated_order_quantity))
           FILTER (WHERE calculated_order_quantity > 0)                      AS approved_capped,
       sum(calculated_order_quantity) FILTER (WHERE calculated_order_quantity > 0) AS calculated_order,
       sum(quantity_approved)                                                AS pipeline_assumed
FROM fpm
GROUP BY facility_code, facility_type_code, product_code, product_name, tracer,
         district_code, district_name, region_id, region_name, programme_id, programme_name, month_id
