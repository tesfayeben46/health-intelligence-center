/*
    lnk_hcmis_dispatch: the dispatch movement fact edge.
    Grain: one row per movement (movement by from-hub by to-facility by period).
    FK mapping:
        hk_hcmis_dispatch          <- movement_id  (the movement hub)
        hk_hcmis_dispatch_hub      <- from_hub_id  (supplying EPSS hub)
        hk_hcmis_dispatch_facility <- to_node_id   (receiving facility)
        hk_hcmis_dispatch_period   <- period_id    (planning period)
    Movement measures + commodity_key + vehicle_run_id ride sat_hcmis_dispatch.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_hcmis_dispatch_route',
    src_fk=['hk_hcmis_dispatch','hk_hcmis_dispatch_hub','hk_hcmis_dispatch_facility','hk_hcmis_dispatch_period'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_hcmiswdm__fact_dispatch'
) }}
