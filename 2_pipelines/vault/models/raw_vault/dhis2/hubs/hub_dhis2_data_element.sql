/*
    hub_dhis2_data_element: DHIS2 metric definition. BK = id (DHIS2 UID).
    Loaded from both the data_elements dim view and the data_value_sets
    fact feed (covers elements that appear in facts before the dim).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_dhis2_data_element',
    src_nk='id',
    src_ldts='load_ts',
    src_source='record_source',
    source_model=['v_stg_dhis2__data_elements', 'v_stg_dhis2__data_elements_from_facts']
) }}
