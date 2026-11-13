{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_hcmis_dispatch_period',
    src_hashdiff={'source_column': 'hd_hcmis_dispatch_period', 'alias': 'hashdiff'},
    src_payload=sat_payload('hcmis_dispatch_period'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_hcmiswdm__dim_dispatchperiod'
) }}
