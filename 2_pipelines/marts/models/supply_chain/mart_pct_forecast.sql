{{ config(materialized='table') }}
{#
    PCT demand forecast: one row per facility by tracer product for the latest
    forecast run that follows the forecast contract (docs/pct-forecast-contract.md)
    (bi_supply.mart_pct_forecast).

    Source: ops_ml predictions via the read seam (41_ml_read.sql). The shared flat
    prediction schema is unchanged; the contract keys travel in attrs and are
    unpacked here. "Latest run" = the most recent successful batch predict run of
    elmis_consumption_forecast from a contract version (one whose backtest was
    written to model_performance); pre-contract versions are ignored.

    Performance: the run is chosen from the small run/performance tables and the
    predictions are filtered with a scalar run_id so postgres_fdw sends the filter
    to ops (a join ships every prediction row across the seam).

    Provenance on every row: model_version, the predict run id, and the MLflow run
    id of the version's training run (the run whose metrics the version was
    selected on). Whether that version is the champion is MLflow's state, read by
    the surface, not frozen here.

    region_id / district come from mart_pct_facility_geo so the geography filter
    narrows forecasts, KPIs and positions alike. Only H1 is produced.
#}

WITH latest_run AS MATERIALIZED (
    SELECT r.id AS run_id, r.model_version, r.mlflow_run_id, r.finished_at
    FROM {{ source('vault_ml', 'ml_run') }} r
    WHERE r.model_name = 'elmis_consumption_forecast'
      AND r.status = 'success'
      AND r.run_label LIKE 'batch:%'
      AND r.model_version IN (
          SELECT mp.model_version FROM {{ source('vault_ml', 'model_performance') }} mp
          WHERE mp.model_name = 'elmis_consumption_forecast'
            AND mp.metric LIKE 'backtest\_%')
    ORDER BY r.finished_at DESC
    LIMIT 1
),

pred AS (
    SELECT p.run_id, p.attrs
    FROM {{ source('vault_ml', 'ml_predictions') }} p
    WHERE p.run_id = (SELECT run_id FROM latest_run)
)

SELECT p.attrs->>'facility_code'                      AS facility_code,
       p.attrs->>'product_code'                       AS product_code,
       p.attrs->>'product_name'                       AS product_name,
       g.region_id, g.region_name, g.district_code, g.district_name,
       p.attrs->>'as_of_month'                        AS as_of_month,
       p.attrs->>'target_month'                       AS target_month,
       (p.attrs->>'horizon')::int                     AS horizon,
       round((p.attrs->>'p10')::numeric, 2)           AS p10,
       round((p.attrs->>'p50')::numeric, 2)           AS p50,
       round((p.attrs->>'p90')::numeric, 2)           AS p90,
       p.attrs->>'demand_class'                       AS demand_class,
       p.attrs->>'method'                             AS method,
       (p.attrs->>'soh_now')::numeric                 AS stock_in_hand,
       (p.attrs->>'amc')::numeric                     AS amc,
       CASE WHEN (p.attrs->>'amc')::numeric > 0
            THEN round(100 * ((p.attrs->>'p50')::numeric - (p.attrs->>'amc')::numeric)
                             / (p.attrs->>'amc')::numeric, 1) END AS forecast_vs_amc_pct,
       p.attrs->>'status'                             AS stock_status,
       l.model_version,
       l.run_id                                       AS predict_run_id,
       l.mlflow_run_id,
       l.finished_at                                  AS predicted_at
FROM pred p
CROSS JOIN latest_run l
LEFT JOIN {{ ref('mart_pct_facility_geo') }} g ON g.facility_code = p.attrs->>'facility_code'
