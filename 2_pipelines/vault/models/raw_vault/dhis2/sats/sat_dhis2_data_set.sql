{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_dhis2_data_set',
    src_hashdiff={'source_column': 'hd_dhis2_data_set', 'alias': 'hashdiff'},
    src_payload=sat_payload('dhis2_data_set'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_dhis2__data_sets'
) }}
