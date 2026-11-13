/*
    hub_facility_conformed: golden-record facility hub.
    BK = conformed_bk (the shared facility_id, else normalised name),
    resolved in v_facility_resolution; every source row is bridged to
    this hub via lnk_same_as_facility. The AutomateDV hub macro dedupes
    on the hash key and keeps the first-seen load_ts.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_facility_conformed', src_nk='conformed_bk', src_ldts='load_ts', src_source='record_source',
    source_model='v_facility_resolution'
) }}
