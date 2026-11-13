{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_climate_week',
    src_nk=['region_id', 'iso_week'],
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_stg_climate__weekly'
) }}
