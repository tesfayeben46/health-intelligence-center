{{ config(materialized='table') }}
{#
    PCT forecast accuracy: the backtest evidence of the model version behind the
    current forecast (bi_supply.mart_pct_forecast_accuracy).

    Source: ops_ml.model_performance via the read seam (41_ml_read.sql). The ML
    runner writes these rows at training time from the model's own fold ledger
    (metric names backtest_<split>_...). This is historical backtest performance:
    the forecast months themselves have no observed accuracy.

    Long format: one row per fold window by split by measure by comparison subject.
    subject_type = 'portfolio' (the selected forecast, P50), 'method' (a comparator
    such as naive_last / trailing3 / eLMIS AMC) or 'demand_class'.
#}

WITH version AS (
    SELECT DISTINCT model_version FROM {{ ref('mart_pct_forecast') }}
),

perf AS (
    SELECT mp.model_version, mp.window_start, mp.window_end, mp.metric, mp.value,
           mp.sample_size, mp.run_id AS train_run_id,
           split_part(mp.metric, '_', 2)                            AS split,
           substr(mp.metric, length('backtest_' || split_part(mp.metric, '_', 2) || '_') + 1) AS rest
    FROM {{ source('vault_ml', 'model_performance') }} mp
    JOIN version v USING (model_version)
    WHERE mp.model_name = 'elmis_consumption_forecast'
      AND mp.metric LIKE 'backtest\_%'
)

SELECT model_version,
       to_char(window_start, 'YYYY-MM')                              AS as_of_month,
       to_char(window_end, 'YYYY-MM')                                AS target_month,
       split,
       CASE WHEN rest LIKE 'wape\_class\_%' OR rest LIKE 'wape\_%' THEN 'wape' ELSE rest END AS measure,
       CASE WHEN rest LIKE 'wape\_class\_%' THEN 'demand_class'
            WHEN rest LIKE 'wape\_%'        THEN 'method'
            ELSE 'portfolio' END                                     AS subject_type,
       CASE WHEN rest LIKE 'wape\_class\_%' THEN substr(rest, length('wape_class_') + 1)
            WHEN rest LIKE 'wape\_%'        THEN substr(rest, length('wape_') + 1)
            ELSE 'selected' END                                      AS subject,
       round(value, 4)                                               AS value,
       sample_size,
       train_run_id
FROM perf
