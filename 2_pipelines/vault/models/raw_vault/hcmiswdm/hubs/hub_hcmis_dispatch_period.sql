/*
    hub_hcmis_dispatch_period: BK = period_id. One row per monthly dispatch
    planning period (its label + plan-level KPI/savings summary ride the sat).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_dispatch_period', src_nk='period_id',
    src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__dim_dispatchperiod']
) }}
