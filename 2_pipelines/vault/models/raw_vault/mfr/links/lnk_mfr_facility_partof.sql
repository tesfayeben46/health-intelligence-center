/*
    lnk_mfr_facility_partof: admin-hierarchy link.
    Child Location (facility) -> parent Organization (admin-tree leaf node:
    woreda / zone / region / country). All hashes are pre-computed in
    v_stg_mfr__facilities (parent side = hk_mfr_organization, since the
    parent is an Organization); rows without a parent have a NULL FK and
    are dropped by the AutomateDV link macro.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_mfr_facility_partof',
    src_fk=['hk_mfr_facility', 'hk_mfr_organization'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mfr__facilities'
) }}
