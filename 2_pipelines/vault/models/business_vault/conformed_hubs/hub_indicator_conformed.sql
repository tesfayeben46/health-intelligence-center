/*
    hub_indicator_conformed: HMIS indicator hub. BK = indicator_id (the
    shared identifier baked into every source by the entity model),
    staged in v_indicator_resolution.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_indicator_conformed', src_nk='indicator_id', src_ldts='load_ts', src_source='record_source',
    source_model='v_indicator_resolution'
) }}
