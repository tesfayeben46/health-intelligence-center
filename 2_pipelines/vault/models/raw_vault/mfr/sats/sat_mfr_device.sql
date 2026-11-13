{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_mfr_device',
    src_hashdiff={'source_column': 'hd_mfr_device', 'alias': 'hashdiff'},
    src_payload=sat_payload('mfr_device'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mfr__devices'
) }}
