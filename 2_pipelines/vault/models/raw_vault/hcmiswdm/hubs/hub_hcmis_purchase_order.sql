/*
    hub_hcmis_purchase_order: BK = purchase_order_id. One row per HCMIS
    vaccine-module purchase order (the procurement pipeline). item_id /
    environment_id ride along the stage view as conformed foreign hash keys
    (hk_hcmis_item / hk_hcmis_environment) so the mart joins commodity + hub.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_purchase_order', src_nk='purchase_order_id',
    src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__vaccine_purchaseorder']
) }}
