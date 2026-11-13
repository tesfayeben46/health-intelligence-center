/*
    lnk_same_as_facility: Same-As Link bridging every source facility hub
    to hub_facility_conformed. One edge per (source system, source record,
    conformed entity). Keys only, per DV2.0: the descriptive resolution
    attributes (source_system, match_strategy, names) live on
    v_facility_resolution, not on the link.

    hk_source_facility spans multiple parent hubs (hub_hcmis_facility,
    hub_mbrana_facility, hub_mbrana_ussd_facility, hub_dhis2_org_unit,
    hub_mfr_facility, hub_mfr_organization), so no single relationships
    test can cover it. The PK hash (computed in the stage view) embeds
    source_system because those hubs hash unqualified BKs whose integer
    key spaces overlap; see the stage-view header.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_same_as_facility',
    src_fk=['hk_source_facility', 'hk_facility_conformed'],
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_facility_resolution'
) }}
