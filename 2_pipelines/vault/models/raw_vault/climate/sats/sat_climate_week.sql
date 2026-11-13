{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_climate_week',
    src_hashdiff={'source_column': 'hd_climate_week', 'alias': 'hashdiff'},
    src_payload=sat_payload('climate_week'),
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_stg_climate__weekly'
) }}
