/*
    hub_hcmis_dispatch_hub: BK = hub_id. One row per EPSS dispatch-plan hub
    (warehouse) in the Supply Control Tower network.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_dispatch_hub', src_nk='hub_id',
    src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__dim_dispatchhub']
) }}
