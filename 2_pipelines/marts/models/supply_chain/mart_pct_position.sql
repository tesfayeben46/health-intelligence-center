{{ config(materialized='table') }}
{#
    PCT projected position: facility by tracer product by programme at the anchor
    month (the latest month in the data: August 2018)
    (bi_supply.mart_pct_position). Two views side by side on every row:

    AMC view: eLMIS's own AMC and max/min policy.
      target_stock    = max stock months (facility type) * AMC   (order-up-to)
      emergency_level = emergency order point months by AMC
      risk_band, replenishment_*, surplus, district redistribution.

    Planning view (docs/pct-forecast-contract.md):
      planning_rate = the champion forecast's P50 (monthly demand, split across
      programmes by their share of recent demand), except lumpy series, series
      without a forecast, and any forecast that is not from the live champion,
      which stay on AMC. planning_basis says which, on every row.
      planning_target_stock / planning_emergency_level / planning_replenishment_*
      apply the same eLMIS policy to planning_rate.
      h1_risk: the one-month stockout test. Stock below P50 = likely short next
      month; for critical tracers (Global Fund S8 categories) stock below P90 = at
      risk (≥ 10% chance); no stock and no demand expected = no_demand_expected
      (never "covered"). P90 is a one-month quantile: it is a risk test here,
      never multiplied into an order, which would inflate orders.
      A zero P50 keeps its zero order instead of falling back to AMC.
      "Live champion" = the model version of the latest scheduled batch run
      (the nightly job predicts with @champion), so a rollback moves rows back to
      AMC after the next nightly.
      Snapshot on every row: forecast model version, predict run, MLflow run.

    Lead time is an assumption (seed pct_policy_parameters). Replenishment is given
    excluding the pipeline (primary) and including the assumed pipeline
    (mart_pct_demand_month.pipeline_assumed, a labelled planning assumption).
    No monetary value: eLMIS prices mix per-unit and per-pack semantics with no
    currency. Packs are rounded only where the pack size exceeds 1.

    Not in the data, and therefore not modelled: true lead times, OTIF, route
    times, vehicle capacity, expiry, warehouse availability.
#}

WITH params AS (
    SELECT max(value) FILTER (WHERE parameter = 'lead_time_days')       AS lead_time_days,
           max(value) FILTER (WHERE parameter = 'review_period_months') AS review_period_months
    FROM {{ ref('pct_policy_parameters') }}
),

anchor AS (
    SELECT max(month_id) AS month_id FROM {{ ref('mart_pct_demand_month') }}
),

recent AS (
    -- demand in the 3 months ending at the anchor: distinguishes "no demand" from "AMC missing"
    SELECT d.facility_code, d.product_code, d.programme_id, sum(d.adjusted_demand) AS recent_demand
    FROM {{ ref('mart_pct_demand_month') }} d, anchor a
    WHERE d.tracer
      AND d.period_start > (a.month_id || '-01')::date - interval '3 months'
    GROUP BY 1, 2, 3
),

policy AS (
    SELECT h.code AS facility_type_code, s.nominal_max_months AS max_stock_months, s.nominal_eop AS eop_months
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_facility_type') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_facility_type_current') }} s USING (hk_elmis_hist_facility_type)
),

pack AS (
    SELECT h.code AS product_code, s.pack_size
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_product') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_product_current') }} s USING (hk_elmis_hist_product)
),

live_version AS (
    SELECT r.model_version
    FROM {{ source('vault_ml', 'ml_run') }} r
    WHERE r.model_name = 'elmis_consumption_forecast'
      AND r.status = 'success'
      AND r.run_label LIKE 'batch:%'
      AND r.trigger_kind = 'schedule'
    ORDER BY r.finished_at DESC
    LIMIT 1
),

fc AS (
    SELECT f.facility_code, f.product_code, f.p10, f.p50, f.p90, f.demand_class,
           f.model_version, f.predict_run_id, f.mlflow_run_id,
           coalesce(f.model_version = (SELECT model_version FROM live_version), false) AS is_live_champion
    FROM {{ ref('mart_pct_forecast') }} f
),

critical AS (
    SELECT product_code, gf_category FROM {{ ref('pct_gf_tracer_category') }}
),

base AS (
    SELECT d.facility_code, d.facility_type_code, d.product_code, d.product_name,
           d.programme_id, d.programme_name, d.district_code, d.district_name,
           d.region_id, d.region_name, d.month_id AS as_of_month,
           d.stock_in_hand, d.amc, d.pipeline_assumed,
           coalesce(r.recent_demand, 0)          AS recent_demand,
           po.max_stock_months, po.eop_months, pk.pack_size,
           pa.lead_time_days, pa.review_period_months
    FROM {{ ref('mart_pct_demand_month') }} d
    JOIN anchor a ON a.month_id = d.month_id
    LEFT JOIN recent r USING (facility_code, product_code, programme_id)
    LEFT JOIN policy po USING (facility_type_code)
    LEFT JOIN pack   pk USING (product_code)
    CROSS JOIN params pa
    WHERE d.tracer
),

sized AS (
    SELECT b.*,
           b.max_stock_months * b.amc                                   AS target_stock,
           b.eop_months * b.amc                                         AS emergency_level,
           CASE WHEN b.amc > 0 THEN round(b.stock_in_hand / b.amc, 2) END AS months_of_stock,
           CASE
             WHEN b.stock_in_hand IS NULL                               THEN 'unknown'
             WHEN b.stock_in_hand <= 0                                  THEN 'stocked_out'
             WHEN coalesce(b.amc, 0) <= 0 AND b.recent_demand > 0       THEN 'unknown'
             WHEN coalesce(b.amc, 0) <= 0                               THEN 'no_recent_demand'
             WHEN b.stock_in_hand / b.amc < b.eop_months                THEN 'below_eop'
             WHEN b.stock_in_hand / b.amc <= b.max_stock_months         THEN 'within_policy'
             ELSE 'over_max'
           END                                                          AS risk_band,
           (b.stock_in_hand IS NULL)                                    AS flag_missing_stock,
           (coalesce(b.amc, 0) <= 0 AND b.recent_demand > 0)            AS flag_amc_missing,
           (coalesce(b.amc, 0) <= 0 AND b.recent_demand <= 0)           AS flag_no_recent_demand,
           (b.pack_size IS NULL)                                        AS flag_pack_size_missing
    FROM base b
),

planned AS (
    SELECT s.*,
           fc.demand_class, fc.model_version AS forecast_model_version,
           fc.predict_run_id AS forecast_predict_run_id, fc.mlflow_run_id AS forecast_mlflow_run_id,
           coalesce(fc.is_live_champion, false)                         AS forecast_is_live_champion,
           (cr.product_code IS NOT NULL)                                AS critical_tracer,
           cr.gf_category,
           CASE WHEN sum(s.recent_demand) OVER k > 0 THEN s.recent_demand / sum(s.recent_demand) OVER k
                ELSE 1.0 / count(*) OVER k END                          AS programme_share,
           CASE WHEN fc.facility_code IS NULL          THEN 'elmis_amc_no_forecast'
                WHEN NOT fc.is_live_champion           THEN 'elmis_amc_not_champion'
                WHEN fc.demand_class = 'lumpy'         THEN 'elmis_amc_lumpy'
                ELSE 'forecast_p50' END                                 AS planning_basis,
           fc.p50, fc.p90
    FROM sized s
    LEFT JOIN fc USING (facility_code, product_code)
    LEFT JOIN critical cr USING (product_code)
    WINDOW k AS (PARTITION BY s.facility_code, s.product_code)
),

rated AS (
    SELECT p.*,
           p.p50 * p.programme_share AS forecast_p50_share,
           p.p90 * p.programme_share AS forecast_p90_share,
           CASE WHEN p.planning_basis = 'forecast_p50' THEN p.p50 * p.programme_share
                WHEN p.amc > 0                         THEN p.amc END AS planning_rate
    FROM planned p
),

need AS (
    SELECT s.*,
           CASE WHEN s.amc > 0 AND s.stock_in_hand IS NOT NULL
                THEN greatest(s.target_stock - greatest(s.stock_in_hand, 0), 0) END AS replenishment_units_excl_pipeline,
           CASE WHEN s.amc > 0 AND s.stock_in_hand IS NOT NULL
                THEN greatest(s.target_stock - greatest(s.stock_in_hand, 0) - coalesce(s.pipeline_assumed, 0), 0) END AS replenishment_units_incl_pipeline,
           CASE WHEN s.amc > 0 AND s.stock_in_hand IS NOT NULL
                THEN greatest(s.stock_in_hand - s.target_stock, 0) END AS surplus_units,
           CASE WHEN s.amc > 0 AND s.stock_in_hand IS NOT NULL
                THEN s.stock_in_hand - s.amc * s.lead_time_days / 30.0 END AS projected_stock_at_lead_time,
           s.max_stock_months * s.planning_rate                         AS planning_target_stock,
           s.eop_months * s.planning_rate                               AS planning_emergency_level,
           CASE WHEN s.planning_rate IS NOT NULL AND s.stock_in_hand IS NOT NULL
                THEN greatest(s.max_stock_months * s.planning_rate - greatest(s.stock_in_hand, 0), 0) END AS planning_replenishment_units_excl_pipeline,
           CASE WHEN s.planning_rate IS NOT NULL AND s.stock_in_hand IS NOT NULL
                THEN greatest(s.max_stock_months * s.planning_rate - greatest(s.stock_in_hand, 0) - coalesce(s.pipeline_assumed, 0), 0) END AS planning_replenishment_units_incl_pipeline,
           CASE WHEN s.planning_rate IS NOT NULL AND s.stock_in_hand IS NOT NULL
                THEN greatest(s.stock_in_hand - s.max_stock_months * s.planning_rate, 0) END AS planning_surplus_units,
           CASE WHEN s.planning_basis IN ('elmis_amc_no_forecast', 'elmis_amc_not_champion')
                  OR s.stock_in_hand IS NULL                            THEN 'not_assessed'
                WHEN s.stock_in_hand < s.forecast_p50_share             THEN 'likely_short'
                WHEN s.critical_tracer AND s.stock_in_hand < s.forecast_p90_share THEN 'critical_at_risk'
                WHEN s.stock_in_hand <= 0                               THEN 'no_demand_expected'
                ELSE 'covered' END                                      AS h1_risk
    FROM rated s
),

district AS (
    SELECT district_code, product_code,
           sum(surplus_units)                     AS district_surplus_units,
           sum(replenishment_units_excl_pipeline) AS district_need_units,
           sum(planning_surplus_units)                     AS district_planning_surplus_units,
           sum(planning_replenishment_units_excl_pipeline) AS district_planning_need_units
    FROM need GROUP BY 1, 2
)

SELECT n.facility_code, n.facility_type_code, n.product_code, n.product_name,
       n.programme_id, n.programme_name, n.district_code, n.district_name,
       n.region_id, n.region_name, n.as_of_month,
       n.stock_in_hand, n.amc, n.recent_demand, n.months_of_stock, n.risk_band,
       n.max_stock_months, n.eop_months, n.lead_time_days, n.review_period_months, n.pack_size,
       round(n.target_stock, 2)     AS target_stock,
       round(n.emergency_level, 2)  AS emergency_level,
       round(n.projected_stock_at_lead_time, 2) AS projected_stock_at_lead_time,
       n.pipeline_assumed,
       round(n.replenishment_units_excl_pipeline, 2) AS replenishment_units_excl_pipeline,
       round(n.replenishment_units_incl_pipeline, 2) AS replenishment_units_incl_pipeline,
       CASE WHEN n.pack_size > 1 THEN ceil(n.replenishment_units_excl_pipeline / n.pack_size)
            ELSE ceil(n.replenishment_units_excl_pipeline) END  AS replenishment_packs_excl_pipeline,
       round(n.surplus_units, 2)    AS surplus_units,
       round(d.district_surplus_units, 2) AS district_surplus_units,
       round(d.district_need_units, 2)    AS district_need_units,
       round(least(d.district_surplus_units, d.district_need_units), 2) AS district_redistributable_units,
       -- planning view
       n.planning_basis, n.critical_tracer, n.gf_category, n.demand_class,
       round(n.programme_share, 4)            AS programme_share,
       round(n.forecast_p50_share, 2)         AS forecast_p50,
       round(n.forecast_p90_share, 2)         AS forecast_p90,
       round(n.planning_rate, 2)              AS planning_rate,
       round(n.planning_target_stock, 2)      AS planning_target_stock,
       round(n.planning_emergency_level, 2)   AS planning_emergency_level,
       round(n.planning_replenishment_units_excl_pipeline, 2) AS planning_replenishment_units_excl_pipeline,
       round(n.planning_replenishment_units_incl_pipeline, 2) AS planning_replenishment_units_incl_pipeline,
       CASE WHEN n.pack_size > 1 THEN ceil(n.planning_replenishment_units_excl_pipeline / n.pack_size)
            ELSE ceil(n.planning_replenishment_units_excl_pipeline) END AS planning_replenishment_packs_excl_pipeline,
       round(n.planning_replenishment_units_excl_pipeline - n.replenishment_units_excl_pipeline, 2) AS planning_delta_vs_amc_units,
       round(least(d.district_planning_surplus_units, d.district_planning_need_units), 2) AS planning_district_redistributable_units,
       n.h1_risk,
       n.forecast_model_version, n.forecast_predict_run_id, n.forecast_mlflow_run_id, n.forecast_is_live_champion,
       n.flag_missing_stock, n.flag_amc_missing, n.flag_no_recent_demand, n.flag_pack_size_missing,
       (n.planning_basis = 'forecast_p50' AND coalesce(n.forecast_p50_share, 0) = 0) AS flag_forecast_zero_demand
FROM need n
LEFT JOIN district d USING (district_code, product_code)
