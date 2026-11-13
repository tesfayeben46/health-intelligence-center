/*
    hub_hcmis_date: BK = date_key. Loaded from the date dim plus the daily
    stock fact (whose stage aliases date_id AS date_key), so a date seen
    only in facts still gets its hub row.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_date', src_nk='date_key', src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__dim_date',
                  'v_stg_hcmiswdm__fact_dailyrecord']
) }}
