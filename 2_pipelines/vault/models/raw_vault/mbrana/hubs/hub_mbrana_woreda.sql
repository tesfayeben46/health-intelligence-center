{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_mbrana_woreda', src_nk='id', src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mbrana__dbo_woreda'
) }}
