/*
    lnk_mbrana_zone_region: admin tree edge: zone -> region.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_zone_region',
    src_fk=['hk_mbrana_zone', 'hk_mbrana_region_fk'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mbrana__dbo_zone'
) }}
