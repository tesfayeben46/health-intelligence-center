{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_vaccine_record', src_nk='daily_record_id', src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_hcmiswdm__vaccine_dailyrecord'
) }}
