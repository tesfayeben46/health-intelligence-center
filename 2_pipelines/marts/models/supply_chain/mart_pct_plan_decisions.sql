{{ config(materialized='table') }}
{#
    PCT plan decisions (bi_supply.mart_pct_plan_decisions): every planner verdict
    on a Supply Control Tower plan line, current and superseded, as warehouse facts
    (read seam 43_pct_plan_read.sql over ops_plan.plan_decision). Each row carries the snapshot taken
    when it was decided, so what was approved never changes with a mart rebuild.
    decided_by and note are classified (restricted: tokenized / dropped) in
    column_classification.yaml.
#}

SELECT d.decision_id::text                                   AS decision_id,
       d.plan_period,
       d.movement_id,
       d.facility_code,
       d.product_code,
       d.from_hub_id,
       d.snapshot -> 'line' ->> 'region_id'                  AS region_id,
       d.decision,
       d.units_recommended,
       d.snapshot -> 'line' ->> 'demand_basis'               AS demand_basis,
       d.snapshot -> 'forecast' ->> 'model_version'          AS forecast_model_version,
       d.snapshot -> 'forecast' ->> 'mlflow_run_id'          AS forecast_mlflow_run_id,
       d.recorded_from                                       AS decided_at,
       d.recorded_to                                         AS superseded_at,
       (d.recorded_to IS NULL)                               AS is_current,
       d.decided_by,
       d.note,
       d.snapshot::text                                      AS snapshot_json
FROM {{ source('vault_plan', 'plan_decision') }} d
