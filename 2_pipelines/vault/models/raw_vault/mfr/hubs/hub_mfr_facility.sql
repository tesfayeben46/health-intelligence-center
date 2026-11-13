/*
    hub_mfr_facility: MFR FHIR Location.
    BK = fhir_id (FHIR resource id, opaque).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_mfr_facility',
    src_nk='fhir_id',
    src_ldts='load_ts',
    src_source='record_source',
    source_model=['v_stg_mfr__facilities']
) }}
