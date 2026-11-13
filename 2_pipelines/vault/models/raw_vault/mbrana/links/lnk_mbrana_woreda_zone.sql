/*
    lnk_mbrana_woreda_zone: admin tree edge: woreda -> zone.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_woreda_zone',
    src_fk=['hk_mbrana_woreda', 'hk_mbrana_zone_fk'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mbrana__dbo_woreda'
) }}
