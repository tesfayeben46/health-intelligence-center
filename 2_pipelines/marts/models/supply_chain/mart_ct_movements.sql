{{ config(materialized='table') }}
{#
    Supply Control Tower movements (bi_supply.mart_ct_movements), Zambia eLMIS.
    One movement per facility by tracer product with a planned order next month
    (planned = true), plus every at-risk facility-product the plan leaves without
    an order (planned = false, units 0) so "shortages covered" is measured;
    from the planning view of bi_supply.mart_pct_position (programmes summed):
    units = planned order (dispensing units), amc = the monthly demand the plan
    used (forecast median, or eLMIS AMC where the forecast does not apply, see
    demand_basis), mos / days_to_stockout from stock on hand at that rate.
    From-hub = the facility's district store (inferred link, see mart_ct_nodes).
    lane_km is straight-line (no road data). Stops are sequenced by a sweep
    around the district store (vehicle_run_id = one district run per period).
    priority = 100 * (1 - months of stock / max stock months), clamped to 0..100.
    why_json follows the panel's convention: signal is demand (negative),
    prePlan = opening + signal, inbound = the order, postPlan = prePlan + inbound.
    No cost data exists: cost / fill columns are NULL.
#}

WITH pos AS (
    SELECT facility_code, product_code,
           max(product_name)                               AS product_name,
           min(programme_id)                               AS programme_id,
           max(as_of_month)                                AS as_of_month,
           sum(stock_in_hand)                              AS soh,
           sum(planning_rate)                              AS rate,
           sum(planning_replenishment_units_excl_pipeline) AS units,
           max(max_stock_months)                           AS max_months,
           CASE WHEN bool_and(planning_basis = 'forecast_p50') THEN 'forecast'
                WHEN bool_or(planning_basis = 'forecast_p50') THEN 'forecast_and_amc'
                ELSE 'elmis_amc' END                       AS demand_basis
           , bool_or(h1_risk IN ('likely_short', 'critical_at_risk')) AS at_risk
    FROM {{ ref('mart_pct_position') }}
    GROUP BY facility_code, product_code
    HAVING coalesce(sum(planning_replenishment_units_excl_pipeline), 0) > 0
        OR bool_or(h1_risk IN ('likely_short', 'critical_at_risk'))
),

geo AS (
    SELECT * FROM {{ ref('mart_pct_facility_geo') }}
),

centre AS (
    -- where the district's facilities actually are (GPS-sourced points only)
    SELECT district_code, avg(lat) AS lat, avg(lng) AS lng
    FROM geo WHERE coord_basis = 'source' GROUP BY district_code
),

dho AS (
    -- same rule as mart_ct_nodes: a DHO with real GPS, nearest the district's facilities
    SELECT DISTINCT ON (g.district_code) g.district_code, g.facility_code, g.lat, g.lng
    FROM geo g LEFT JOIN centre c USING (district_code)
    WHERE g.facility_type_code = 'DHO'
    ORDER BY g.district_code, (g.coord_basis = 'source') DESC,
             power(g.lat - coalesce(c.lat, g.lat), 2) + power(g.lng - coalesce(c.lng, g.lng), 2), g.facility_code
),

district AS (
    SELECT district_code, avg(lat) AS lat, avg(lng) AS lng FROM geo GROUP BY district_code
),

hub AS (
    SELECT d.district_code, 'H-' || coalesce(dho.facility_code, 'D' || d.district_code) AS hub_id,
           coalesce(dho.lat, d.lat) AS lat, coalesce(dho.lng, d.lng) AS lng
    FROM district d LEFT JOIN dho USING (district_code)
),

fc AS (
    -- the forecast behind a pure forecast-sized line (same grain, unique per side)
    SELECT facility_code, product_code, p10, p90, model_version, mlflow_run_id
    FROM {{ ref('mart_pct_forecast') }}
),

leg AS (
    SELECT p.*, g.region_name, g.region_id, g.district_code, h.hub_id,
           to_char((p.as_of_month || '-01')::date + interval '1 month', 'YYYY-MM') AS period_id,
           CASE WHEN p.demand_basis = 'forecast' THEN fc.p10 END AS fc_p10,
           CASE WHEN p.demand_basis = 'forecast' THEN fc.p90 END AS fc_p90,
           CASE WHEN p.demand_basis = 'forecast' THEN fc.model_version END AS fc_model_version,
           CASE WHEN p.demand_basis = 'forecast' THEN fc.mlflow_run_id END AS fc_mlflow_run_id,
           2 * 6371 * asin(sqrt(power(sin(radians(g.lat - h.lat) / 2), 2)
               + cos(radians(h.lat)) * cos(radians(g.lat)) * power(sin(radians(g.lng - h.lng) / 2), 2))) AS km,
           atan2(g.lat - h.lat, g.lng - h.lng) AS bearing
    FROM pos p
    JOIN geo g USING (facility_code)
    JOIN hub h USING (district_code)
    LEFT JOIN fc USING (facility_code, product_code)
),

seq AS (
    SELECT l.*, (coalesce(units, 0) > 0) AS planned_order,
           CASE WHEN coalesce(units, 0) > 0 THEN dense_rank() OVER (PARTITION BY hub_id, period_id, (coalesce(units, 0) > 0)
                ORDER BY bearing, km, facility_code) END AS stop_seq
    FROM leg l
)

SELECT
    facility_code || ':' || product_code                AS movement_id,
    period_id,
    hub_id                                              AS from_hub_id,
    facility_code                                       AS to_node_id,
    region_name                                         AS region,
    region_id,
    product_name                                        AS drug,
    product_name                                        AS drug_short,
    product_code                                        AS commodity_key,
    programme_id,
    ceil(coalesce(greatest(units, 0), 0))::int          AS units,
    round(rate)::int                                    AS amc,
    CASE WHEN rate > 0 THEN round((greatest(soh, 0) / rate)::numeric, 2) END AS mos,
    CASE WHEN rate > 0 THEN floor(greatest(soh, 0) / (rate / 30.0))::int END AS days_to_stockout,
    CASE WHEN rate > 0 AND max_months > 0
         THEN round(100 * greatest(0, least(1, 1 - (greatest(soh, 0) / rate) / max_months)))::int
         ELSE 0 END                                     AS priority,
    round(km)::int                                      AS lane_km,
    NULL::int                                           AS cost_units,
    stop_seq::int                                       AS sequence_index,
    CASE WHEN planned_order THEN 'R-' || hub_id || '-' || period_id END AS vehicle_run_id,
    NULL::numeric                                       AS fill_rate,
    hub_id                                              AS nearest_hub_id,
    NULL::text                                          AS alt_hub_id,
    'district'::text                                    AS reason,
    0                                                   AS assign_delta_km,
    NULL::int                                           AS assign_delta_cost,
    hub_id                                              AS baseline_hub_id,
    round(km)::int                                      AS baseline_km,
    planned_order                                       AS planned,
    false                                               AS repaired,
    false                                               AS capped,
    json_build_object('opening', round(soh), 'signal', -round(rate), 'prePlan', round(soh - rate),
                      'inbound', ceil(coalesce(greatest(units, 0), 0)),
                      'postPlan', round(soh - rate) + ceil(coalesce(greatest(units, 0), 0)))::text AS why_json,
    demand_basis,
    round(fc_p10)::int                                  AS forecast_p10,
    round(fc_p90)::int                                  AS forecast_p90,
    fc_model_version                                    AS forecast_model_version,
    fc_mlflow_run_id                                    AS forecast_mlflow_run_id,
    at_risk
FROM seq
