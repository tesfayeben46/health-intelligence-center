/*
    hub_mfr_organization: MFR FHIR Organization. Every admin-tree level
    (country / region / zone / woreda) and every facility owner is an
    Organization in the seed.
    BK = fhir_id (FHIR resource id).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_mfr_organization',
    src_nk='fhir_id',
    src_ldts='load_ts',
    src_source='record_source',
    source_model=['v_stg_mfr__organizations']
) }}
