/*
    hub_commodity_conformed: golden-record commodity hub.
    BK = conformed_bk (the cluster's normalised name), resolved in
    v_commodity_resolution; source rows are bridged via
    lnk_same_as_commodity. The AutomateDV hub macro dedupes on the hash
    key and keeps the first-seen load_ts.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_commodity_conformed', src_nk='conformed_bk', src_ldts='load_ts', src_source='record_source',
    source_model='v_commodity_resolution'
) }}
