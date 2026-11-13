{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_ethioemr_observation',
    src_hashdiff={'source_column': 'hd_ethioemr_observation', 'alias': 'hashdiff'},
    src_payload=sat_payload('ethioemr_observation'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_ethioemr__observation'
) }}
