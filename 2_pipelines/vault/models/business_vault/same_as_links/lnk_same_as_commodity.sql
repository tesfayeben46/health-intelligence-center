/*
    lnk_same_as_commodity: Same-As Link bridging every source commodity
    hub to hub_commodity_conformed. One edge per (source system, source
    record, conformed entity). Keys only, per DV2.0: the descriptive
    resolution attributes live on v_commodity_resolution, not on the link.

    hk_source_commodity spans two parent hubs (hub_hcmis_commodity,
    hub_mbrana_commodity), so no single relationships test can cover it.
    The PK hash (computed in the stage view) embeds source_system because
    those hubs hash unqualified integer BKs (both 1..34); dropping it would
    collide edges. See the stage-view header.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_same_as_commodity',
    src_fk=['hk_source_commodity', 'hk_commodity_conformed'],
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_commodity_resolution'
) }}
