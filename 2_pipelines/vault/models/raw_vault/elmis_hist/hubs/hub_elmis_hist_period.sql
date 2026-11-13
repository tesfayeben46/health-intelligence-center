{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_elmis_hist_period', src_nk='source_id', src_ldts='load_ts',
    src_source='record_source', source_model=['v_stg_elmis_hist__reference_reporting_period']
) }}
