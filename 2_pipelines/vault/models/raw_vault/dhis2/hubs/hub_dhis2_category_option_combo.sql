/*
    hub_dhis2_category_option_combo: BK = id (DHIS2 UID). Loaded from the
    dim view plus the data_value_sets fact feed, which unions both roles
    the fact plays against this hub (category_option_combo and
    attribute_option_combo).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_dhis2_category_option_combo', src_nk='id', src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_dhis2__category_option_combos', 'v_stg_dhis2__category_option_combos_from_facts']
) }}
