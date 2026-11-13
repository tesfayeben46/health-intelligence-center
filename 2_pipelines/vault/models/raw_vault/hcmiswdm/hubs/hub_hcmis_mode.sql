{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_mode', src_nk='mode_id', src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_hcmiswdm__fact_dailyrecord'
) }}
