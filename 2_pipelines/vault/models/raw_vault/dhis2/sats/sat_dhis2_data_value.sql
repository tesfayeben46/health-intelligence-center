/*
    Link sat: descriptive attributes of a DHIS2 data value over time.
    AutomateDV inserts a new row only when hd_dhis2_data_value (hash_diff of
    value + stored_by) differs from the most-recent row for this hash_key.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.sat(
    src_pk='hk_dhis2_data_value',
    src_hashdiff={'source_column': 'hd_dhis2_data_value', 'alias': 'hashdiff'},
    src_payload=sat_payload('dhis2_data_value'),
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_stg_dhis2__data_value_sets'
) }}
