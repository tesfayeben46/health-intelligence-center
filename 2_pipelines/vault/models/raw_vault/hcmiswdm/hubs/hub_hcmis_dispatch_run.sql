/*
    hub_hcmis_dispatch_run: BK = run_id. One row per EPSS dispatch-plan vehicle
    run (its stops + road path + fill/cost/ETA ride the sat as JSON/measures).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_dispatch_run', src_nk='run_id',
    src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__fact_dispatchrun']
) }}
