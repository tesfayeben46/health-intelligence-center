{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_mbrana_region',
    src_hashdiff={'source_column': 'hd_mbrana_region', 'alias': 'hashdiff'},
    src_payload=sat_payload('mbrana_region'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mbrana__dbo_region'
) }}
