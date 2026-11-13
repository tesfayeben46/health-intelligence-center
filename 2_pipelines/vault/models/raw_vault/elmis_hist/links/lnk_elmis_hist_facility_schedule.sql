/*
    lnk_elmis_hist_facility_schedule: a facility's reporting schedule per programme
    (source PK = facility x programme x processing schedule; the schedule code is a
    dependent key inside the link hash, as processing_schedule has no hub in this lane).
    Requisition group, direct delivery and drop-off point live on the satellite.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_elmis_hist_facility_schedule',
    src_fk=['hk_elmis_hist_facility','hk_elmis_hist_programme'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_elmis_hist__reference_facility_programme_schedule'
) }}
