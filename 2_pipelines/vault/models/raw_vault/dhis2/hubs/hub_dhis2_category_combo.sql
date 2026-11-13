{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_dhis2_category_combo', src_nk='id', src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_dhis2__category_combos'
) }}
