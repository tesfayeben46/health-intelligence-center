/*
    lnk_hcmis_stock_event: HCMIS daily fact-grain link.
    FK mapping (item by environment by mode by date):
        hk_hcmis_item        <- fact_dailyrecord.item_id
        hk_hcmis_environment <- fact_dailyrecord.environment_id
        hk_hcmis_mode        <- fact_dailyrecord.mode_id
        hk_hcmis_date        <- fact_dailyrecord.date_id  (joins dim_date.date_key)
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_hcmis_stock_event',
    src_fk=['hk_hcmis_item','hk_hcmis_environment','hk_hcmis_mode','hk_hcmis_date'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_hcmiswdm__fact_dailyrecord'
) }}
