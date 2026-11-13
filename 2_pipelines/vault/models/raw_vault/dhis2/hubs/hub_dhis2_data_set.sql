/*
    hub_dhis2_data_set: BK = id (DHIS2 UID). Loaded from both the
    data_sets dim view and the data_value_sets fact feed.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_dhis2_data_set', src_nk='id', src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_dhis2__data_sets', 'v_stg_dhis2__data_sets_from_facts']
) }}
