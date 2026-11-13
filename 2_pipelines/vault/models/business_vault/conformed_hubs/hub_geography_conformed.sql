/*
    hub_geography_conformed: golden-record admin-node hub.
    BK = `geography_id`  (e.g. ET-OR | ZON-OR-… | WOR-OR-…), staged in
    v_geography_resolution. `geography_level` rides along as an extra
    column (not a sat) because it is invariant: a node never changes
    level; the id prefix encodes it.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_geography_conformed', src_nk='geography_id', src_extra_columns='geography_level',
    src_ldts='load_ts', src_source='record_source',
    source_model='v_geography_resolution'
) }}
