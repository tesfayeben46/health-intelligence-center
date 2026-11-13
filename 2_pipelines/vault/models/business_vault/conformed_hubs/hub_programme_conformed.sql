/*
    hub_programme_conformed: programme hub.
    BK = programme_id
    (HIV | TB | EPI | MNCH | NCD | MALARIA | FP | NUTRITION | OTHER),
    staged in v_programme_resolution (the indicator-code -> programme
    single source of truth).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_programme_conformed', src_nk='programme_id', src_ldts='load_ts', src_source='record_source',
    source_model='v_programme_resolution'
) }}
