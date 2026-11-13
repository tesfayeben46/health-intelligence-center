/*
    hub_hcmis_item: BK = item_id. Loaded from the item dim plus both fact
    stages, which hash the same item_id into hk_hcmis_item (an item seen
    only in facts still gets its hub row, so no link FK is orphaned).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_item', src_nk='item_id', src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__dim_item',
                  'v_stg_hcmiswdm__fact_dailyrecord',
                  'v_stg_hcmiswdm__vaccine_dailyrecord']
) }}
