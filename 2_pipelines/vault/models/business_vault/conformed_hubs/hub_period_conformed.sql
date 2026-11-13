/*
    hub_period_conformed: calendar period hub.
    BK = (period_id, period_grain), staged in v_period_resolution.
    period_grain ∈ {monthly, daily}.
    period_id format depends on grain:
        monthly -> 'YYYY-MM'
        daily   -> 'YYYY-MM-DD'
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_period_conformed', src_nk=['period_id', 'period_grain'], src_ldts='load_ts', src_source='record_source',
    source_model='v_period_resolution'
) }}
