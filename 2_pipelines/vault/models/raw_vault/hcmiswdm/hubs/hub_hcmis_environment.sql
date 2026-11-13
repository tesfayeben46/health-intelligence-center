/*
    hub_hcmis_environment: BK = environment_id. No dedicated dim in source;
    environments are observed via both fact stages (each hashes
    environment_id into hk_hcmis_environment).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_environment', src_nk='environment_id', src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__fact_dailyrecord',
                  'v_stg_hcmiswdm__vaccine_dailyrecord',
                  'v_stg_hcmiswdm__dim_environment']
) }}
