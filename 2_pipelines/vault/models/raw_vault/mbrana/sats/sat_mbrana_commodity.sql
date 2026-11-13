{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_mbrana_commodity',
    src_hashdiff={'source_column': 'hd_mbrana_commodity', 'alias': 'hashdiff'},
    src_payload=sat_payload('mbrana_commodity'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mbrana__mflowussd_commodities'
) }}
