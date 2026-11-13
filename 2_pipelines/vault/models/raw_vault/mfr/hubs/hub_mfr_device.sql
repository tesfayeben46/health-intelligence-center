/*
    hub_mfr_device: MFR FHIR Device (facility equipment inventory item).
    BK = device_id (FHIR resource id).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_mfr_device',
    src_nk='device_id',
    src_ldts='load_ts',
    src_source='record_source',
    source_model=['v_stg_mfr__devices']
) }}
