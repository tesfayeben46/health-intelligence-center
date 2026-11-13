/*
    lnk_mfr_org_partof: Organization -> parent Organization.
    Captures the admin hierarchy: country <- region <- zone <- woreda
    (<- facility-owner organisations, where present). All hashes are
    pre-computed in v_stg_mfr__organizations; the parent side is a second
    role on hub_mfr_organization (hk_mfr_organization_parent).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_mfr_org_partof',
    src_fk=['hk_mfr_organization', 'hk_mfr_organization_parent'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mfr__organizations'
) }}
